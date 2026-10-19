# -*- coding: utf-8 -*-
"""Lightweight Monitor UI Client for B518 JetKVM Relay.

Decoupled Tkinter application that connects to the running Core Service
over TCP. Allows operators to inspect live KVM streams, observe station
statuses in real time, and manually trigger diagnostic checks without
interfering with ongoing LabVIEW automated test runs.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

# DPI policy for Windows
try:
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))  # SYSTEM_AWARE
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    _TK_OK = True
except Exception:
    tk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    _TK_OK = False

try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except Exception:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]
    _PIL_OK = False

try:
    import cv2
    _CV2_OK = True
except Exception:
    _CV2_OK = False

# Ensure host-app directory on sys.path
_HOST_APP_DIR = Path(__file__).resolve().parent
if str(_HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_APP_DIR))

from core_channel import CoreChannel, channel_name_for
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog

try:
    from pattern_tools import PatternCropper
except Exception:
    PatternCropper = None  # type: ignore[assignment,misc]

try:
    from stream_view import StreamerWindow
except Exception:
    StreamerWindow = None  # type: ignore[assignment,misc]

try:
    from device_settings_window import DeviceSettingsWindow
except Exception:
    DeviceSettingsWindow = None  # type: ignore[assignment,misc]

logger = logging.getLogger("MonitorUI")


# ==============================================================================
# CoreServiceClient: Thread-Safe TCP Client to Core Service
# ==============================================================================

_STATE_LABELS = {
    "standby": "待命",
    "preparing": "準備中",
    "monitoring": "監控就緒",
    "review": "待確認",
    "complete": "一般完成",
    "manual_stop_complete": "人工停止完成",
}

_EVIDENCE_LABELS = {
    "testing": "測試中",
    "result": "已有結果",
}


def format_manual_check(res: Dict[str, Any]) -> str:
    """Operator text for one inspect reply: Chinese Log state and started channels."""
    status = res.get("status", "error")
    elapsed = res.get("elapsed_sec", 0.0)
    if status == "ok":
        state = res.get("state") or ""
        state_name = _STATE_LABELS.get(state, state or "未知")
        channels = []
        for row in res.get("active") or []:
            evidence = _EVIDENCE_LABELS.get(row.get("evidence", ""), row.get("evidence", ""))
            channels.append(f"通道 {row.get('slot')} {evidence}")
        text = f"診斷結果: {state_name}"
        if channels:
            text += "；" + "、".join(channels)
        if elapsed:
            text += f" (耗時: {elapsed}s)"
        return text
    if status == "timeout":
        return f"診斷結果: TIMEOUT (耗時 {elapsed}s)"
    if res.get("error") == "device_busy":
        return "診斷結果: BUSY (設備忙碌中/LabVIEW測試中)"
    err = res.get("message") or res.get("error") or "未知錯誤"
    return f"診斷錯誤: {err}"


def _flatten(res: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a 1.0 reply's ``data`` into the top level so UI code reads one dict."""
    if not isinstance(res.get("data"), dict):
        return res
    merged = dict(res["data"])
    for key in ("status", "error", "message", "elapsed_sec", "req_id", "device"):
        if key in res:
            merged[key] = res[key]
    return merged


class CoreServiceClient:
    """Monitor UI client to the Core Service.

    Three independent Core connections, one per concern, so a long inspect cannot
    stall status polling, snapshots/stream, or the settings page:
      - status:  core_status / ping (status polling, settings "test connection", restart check)
      - stream:  snapshot (live stream and snapshot button)
      - inspect: inspect (manual check, may wait up to timeout_sec + 4 s)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5000) -> None:
        self.host = host
        self.port = int(port)
        self._status = CoreChannel("status", host, self.port)
        self._stream = CoreChannel("stream", host, self.port)
        self._inspect = CoreChannel("inspect", host, self.port)
        self._poll_lock = threading.Lock()
        self._poll_inflight = False

    def _channels(self) -> Tuple[CoreChannel, ...]:
        return (self._status, self._stream, self._inspect)

    def _channel_for(self, cmd: str) -> CoreChannel:
        return {"status": self._status, "stream": self._stream, "inspect": self._inspect}[channel_name_for(cmd)]

    def set_target(self, host: str, port: int) -> None:
        """Update target host and port. Non-blocking: in-flight requests are cancelled."""
        self.host = host
        self.port = int(port)
        for ch in self._channels():
            ch.set_target(host, port)

    def is_connected(self) -> bool:
        """Check if the status connection appears connected."""
        return self._status.is_connected()

    def connect(self, timeout: float = 2.0) -> bool:
        """Explicitly open the status connection."""
        return self._status.connect(timeout=timeout)

    def disconnect(self) -> None:
        """Drop every Core connection and cancel in-flight requests. Non-blocking."""
        for ch in self._channels():
            ch.abort()

    def send_request(
        self,
        cmd: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Send a newline-delimited JSON command on its channel and return the matched reply."""
        return self._channel_for(cmd).request(cmd, payload, timeout)

    def start_status_poll(self, callback: Any, timeout: float = 2.0) -> bool:
        """Poll core_status on a worker thread unless the previous poll is still in flight.

        Returns False (and sends nothing) when skipped. At most one poll thread exists at a time.
        """
        with self._poll_lock:
            if self._poll_inflight:
                return False
            self._poll_inflight = True

        def worker() -> None:
            try:
                res = self.get_status(timeout=timeout)
            except Exception as exc:
                res = {"status": "error", "message": str(exc)}
            finally:
                with self._poll_lock:
                    self._poll_inflight = False
            callback(res)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def ping(self, timeout: float = 2.0) -> bool:
        """Send a ping check."""
        resp = self.send_request("ping", timeout=timeout)
        return resp.get("status") == "ok"

    def get_status(self, timeout: float = 2.0) -> Dict[str, Any]:
        """Query real-time Core Service status."""
        return self._core_status(timeout, default_status="running")

    def get_stations(self, timeout: float = 2.0) -> Dict[str, Any]:
        """Query configured stations status."""
        return self._core_status(timeout, default_status="ok")

    def _core_status(self, timeout: float, default_status: str) -> Dict[str, Any]:
        """core_status reply flattened; default_status fills "status" when the Core omits it."""
        res = self.send_request("core_status", timeout=timeout)
        if res.get("status") == "ok" and isinstance(res.get("data"), dict):
            merged = dict(res["data"])
            merged["req_id"] = res.get("req_id")
            if "status" not in merged:
                merged["status"] = default_status
            return merged
        return res

    def get_snapshot(
        self,
        device: str,
        format: str = "jpeg",
        quality: int = 80,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Request a snapshot image of a device by its device ID (read-only, no lock)."""
        payload: Dict[str, Any] = {
            "device": device,
            "format": format,
            "quality": quality,
        }
        return _flatten(self.send_request("snapshot", payload, timeout=timeout))

    def manual_check(
        self,
        device: str,
        timeout_sec: float = 5.0,
    ) -> Dict[str, Any]:
        """Trigger a manual visual check command through Core Service."""
        payload: Dict[str, Any] = {
            "device": device,
            "timeout_sec": timeout_sec,
        }
        # inspect is the Monitor UI's read-only check: no device lock, never consumes the round.
        return _flatten(self.send_request("inspect", payload, timeout=timeout_sec + 4.0))


_BaseToplevel: Any = tk.Toplevel if _TK_OK else object
_BaseTk: Any = tk.Tk if _TK_OK else object


# ==============================================================================
# MatchResultsWindow: Readonly viewer for persisted diagnostics
# ==============================================================================

class MatchResultsWindow(_BaseToplevel):
    """Readonly viewer for each device's most recent persisted match diagnostics."""

    def __init__(self, parent: Any, catalog: TemplateCatalog, device: str) -> None:
        if not _TK_OK:
            raise RuntimeError("Tkinter is not available in current environment.")
        super().__init__(parent)
        self.catalog = catalog
        self.title("最近一次匹配結果")
        self.geometry("980x700")
        self.photo: Optional[ImageTk.PhotoImage] = None
        self.summary: Optional[Dict[str, Any]] = None
        self.records: List[Dict[str, Any]] = []

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="設備：").pack(side="left")
        self.device_var = tk.StringVar(value=device)
        self.device_box = ttk.Combobox(
            top,
            textvariable=self.device_var,
            values=catalog.devices(),
            state="readonly",
            width=7,
        )
        self.device_box.pack(side="left")
        self.device_box.bind("<<ComboboxSelected>>", lambda _event: self.load())
        self.status_var = tk.StringVar()
        ttk.Label(top, textvariable=self.status_var).pack(side="left", padx=12)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(body, width=320)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=3)
        self.listbox = tk.Listbox(left, font=("Arial", 11), exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._select)
        self.image_label = ttk.Label(right, text="選擇左側項目以查看疊圖", anchor="center")
        self.image_label.pack(fill="both", expand=True)
        self.load()

    def load(self) -> None:
        self.summary = MatchDiagnostics.load(self.catalog.root, self.device_var.get())
        self.records = [] if self.summary is None else self.summary.get("records", [])
        self.listbox.delete(0, "end")
        if not self.summary:
            self.status_var.set("尚無最近一次診斷結果")
            self.image_label.configure(text="請先執行 Check 或測試操作後再查看。", image="")
            return
        operation = self.summary.get("operation", "")
        state = "完成" if self.summary.get("ok") else "失敗"
        self.status_var.set(f"{operation}：{state}，{len(self.records)} 個匹配步驟")
        for record in self.records:
            score = record.get("score")
            score_text = "n/a" if score is None else f"{score:.3f}"
            status = "命中" if record.get("matched") else "未命中"
            self.listbox.insert("end", f"{record.get('key')} | {score_text} | {status}")
        if self.records:
            self.listbox.selection_set(0)
            self._select()

    def _select(self, _event: Any = None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        record = self.records[selection[0]]
        image_name = record.get("image")
        if not image_name:
            self.image_label.configure(text=record.get("note") or "此步沒有可顯示的影格", image="")
            return
        image_path = (
            MatchDiagnostics.latest_directory(self.catalog.root, self.device_var.get())
            / image_name
        )
        try:
            image = Image.open(image_path)
            image.thumbnail((620, 580))
            self.photo = ImageTk.PhotoImage(image)
            self.image_label.configure(image=self.photo, text="")
        except Exception as exc:
            self.image_label.configure(text=f"無法讀取疊圖：{exc}", image="")


# ==============================================================================
# AtlasUI: Decoupled Lightweight Monitor Client
# ==============================================================================

class AtlasUI(_BaseTk):
    """Lightweight Monitor Client GUI."""

    def __init__(self, core_host: str = "127.0.0.1", core_port: int = 5000) -> None:
        if not _TK_OK:
            raise RuntimeError("Tkinter is not available in current environment.")
        super().__init__()
        self.title("Atlas2-518 Lightweight Monitor Client")

        # Fonts
        if sys.platform == "darwin":
            fam, mono = "PingFang TC", "Menlo"
        elif sys.platform.startswith("win"):
            fam, mono = "Microsoft JhengHei", "Consolas"
        else:
            fam, mono = "Noto Sans CJK TC", "DejaVu Sans Mono"
        self.f_label = (fam, 11)
        self.f_bold = (fam, 11, "bold")
        self.f_entry = (fam, 11)
        self.f_btn = (fam, 11)
        self.f_mono = (mono, 10)
        self.f_large_bold = (fam, 12, "bold")

        # State & Client
        self.client = CoreServiceClient(host=core_host, port=core_port)
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._ui_queue: queue.Queue[Any] = queue.Queue()
        self._templates = TemplateCatalog()

        # Stream & Snapshot state
        self._streaming_active = False
        self._stream_thread: Optional[threading.Thread] = None
        self._latest_pil_image: Optional[Image.Image] = None
        self._latest_tk_photo: Optional[ImageTk.PhotoImage] = None
        self._latest_raw_frame: Any = None
        self._streamer_window: Optional[StreamerWindow] = None

        # Stations cache
        # Device list as reported by Core Service's core_status; empty until connected.
        self._stations: List[Dict[str, Any]] = []
        self._rendered_device_ids: List[str] = []
        self._station_widgets: Dict[str, Dict[str, Any]] = {}

        # Polling
        self._is_closing = False

        self._build_widgets()

        # Geometry
        self.update_idletasks()
        self.geometry("860x920")
        self.minsize(800, 750)

        # Scheduled jobs
        self.after(100, self._drain_queues)
        self.after(500, self._poll_status)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        PADX = 10
        GAP = 6

        # ----------------------------------------------------------------------
        # Top Bar: Core Service Connection
        # ----------------------------------------------------------------------
        top_frame = tk.LabelFrame(self, text="Core Service 連線設定", font=self.f_bold, padx=8, pady=6)
        top_frame.pack(fill="x", padx=PADX, pady=(GAP, GAP))

        tk.Label(top_frame, text="服務位址:", font=self.f_label).pack(side="left")
        self.e_core_addr = tk.Entry(top_frame, font=self.f_entry, width=18, justify="center")
        self.e_core_addr.insert(0, f"{self.client.host}:{self.client.port}")
        self.e_core_addr.pack(side="left", padx=6)

        self.btn_connect = tk.Button(
            top_frame, text="重新連線", font=self.f_btn, command=self.on_toggle_connect
        )
        self.btn_connect.pack(side="left", padx=6)

        self.btn_device_settings = tk.Button(
            top_frame, text="⚙ 設備設定", font=self.f_btn, command=self.on_open_device_settings
        )
        self.btn_device_settings.pack(side="left", padx=6)

        self.lbl_core_status = tk.Label(
            top_frame, text="● 未連線", font=self.f_bold, fg="gray"
        )
        self.lbl_core_status.pack(side="left", padx=12)

        self.lbl_core_info = tk.Label(top_frame, text="", font=self.f_mono, fg="#555")
        self.lbl_core_info.pack(side="right", padx=6)

        # ----------------------------------------------------------------------
        # Station Real-Time Status Dashboard
        # ----------------------------------------------------------------------
        dash_frame = tk.LabelFrame(
            self, text="測試機台即時狀態 (Configured Test Stations)", font=self.f_bold, padx=8, pady=6
        )
        dash_frame.pack(fill="x", padx=PADX, pady=GAP)

        self.stations_container = tk.Frame(dash_frame)
        self.stations_container.pack(fill="x", expand=True)

        self._render_station_cards()

        # ----------------------------------------------------------------------
        # Middle: Split into Live Stream View & Controls
        # ----------------------------------------------------------------------
        mid_paned = ttk.Panedwindow(self, orient="horizontal")
        mid_paned.pack(fill="both", expand=True, padx=PADX, pady=GAP)

        # Left pane: Live Monitor Display
        view_frame = tk.LabelFrame(mid_paned, text="即時畫面監控 (Live Stream / Snapshot)", font=self.f_bold, padx=6, pady=6)
        mid_paned.add(view_frame, weight=3)

        self.canvas_preview = tk.Canvas(view_frame, bg="#1e1e1e", width=480, height=270, highlightthickness=0)
        self.canvas_preview.pack(fill="both", expand=True, padx=4, pady=4)

        view_bottom = tk.Frame(view_frame)
        view_bottom.pack(fill="x", pady=(4, 0))

        self.lbl_preview_info = tk.Label(
            view_bottom, text="尚未擷取影格", font=self.f_mono, fg="#444", anchor="w"
        )
        self.lbl_preview_info.pack(side="left", fill="x", expand=True)

        self.btn_snapshot = tk.Button(
            view_bottom, text="📷 擷取快照", font=self.f_btn, command=self.on_capture_snapshot
        )
        self.btn_snapshot.pack(side="right", padx=4)

        self.btn_live_stream = tk.Button(
            view_bottom, text="▶ 開始串流", font=self.f_btn, command=self.on_toggle_live_stream
        )
        self.btn_live_stream.pack(side="right", padx=4)

        self.btn_streamer_window = tk.Button(
            view_bottom, text="⇱ 獨立視窗", font=self.f_btn, command=self.on_open_streamer_window
        )
        self.btn_streamer_window.pack(side="right", padx=4)

        # Right pane: Diagnostics & Pattern tools
        ctrl_frame = tk.LabelFrame(mid_paned, text="手動診斷與操作 (Diagnostics)", font=self.f_bold, padx=8, pady=6)
        mid_paned.add(ctrl_frame, weight=2)

        # Target selection
        row_target = tk.Frame(ctrl_frame)
        row_target.pack(fill="x", pady=4)
        tk.Label(row_target, text="設備 ID:", font=self.f_label).pack(side="left")
        self.var_sel_device = tk.StringVar(value="")
        self.combo_device = ttk.Combobox(
            row_target,
            textvariable=self.var_sel_device,
            values=[],
            state="readonly",
            width=10,
        )
        self.combo_device.pack(side="left", padx=4)
        self.combo_device.bind("<<ComboboxSelected>>", self._on_device_selected)

        tk.Label(row_target, text="站別:", font=self.f_label).pack(side="left", padx=(8, 0))
        self.var_sel_station = tk.StringVar(value="")
        tk.Label(row_target, textvariable=self.var_sel_station, font=self.f_mono, width=6, anchor="w").pack(side="left", padx=4)

        # Manual Check button
        check_box = tk.LabelFrame(ctrl_frame, text="手動檢查 (Check)", font=self.f_label, padx=6, pady=4)
        check_box.pack(fill="x", pady=6)

        row_check_opt = tk.Frame(check_box)
        row_check_opt.pack(fill="x", pady=2)
        tk.Label(row_check_opt, text="Timeout(s):", font=self.f_label).pack(side="left")
        self.var_timeout = tk.StringVar(value="5.0")
        tk.Entry(row_check_opt, textvariable=self.var_timeout, width=5, font=self.f_entry).pack(side="left", padx=4)

        self.btn_run_check = tk.Button(
            check_box, text="⚡ 執行手動診斷檢查", font=self.f_bold, fg="blue", command=self.on_run_manual_check
        )
        self.btn_run_check.pack(fill="x", pady=4)

        self.lbl_check_result = tk.Label(check_box, text="診斷結果: 尚未執行", font=self.f_bold, fg="#555")
        self.lbl_check_result.pack(anchor="w", pady=2)

        # Pattern creation
        pat_box = tk.LabelFrame(ctrl_frame, text="模板工具 (Pattern)", font=self.f_label, padx=6, pady=4)
        pat_box.pack(fill="x", pady=6)

        self.btn_save_capture = tk.Button(
            pat_box, text="儲存目前影格為 Capture", font=self.f_btn, command=self.on_save_capture
        )
        self.btn_save_capture.pack(fill="x", pady=2)

        self.btn_create_pattern = tk.Button(
            pat_box, text="製作 JetKVM Pattern", font=self.f_btn, command=self.on_create_pattern
        )
        self.btn_create_pattern.pack(fill="x", pady=2)

        self.btn_match_results = tk.Button(
            pat_box, text="查看最近匹配診斷", font=self.f_btn, command=self.on_show_match_results
        )
        self.btn_match_results.pack(fill="x", pady=2)

        # ----------------------------------------------------------------------
        # Bottom: Log Viewer
        # ----------------------------------------------------------------------
        log_frame = tk.LabelFrame(self, text="系統與操作日誌 (Log)", font=self.f_bold, padx=6, pady=4)
        log_frame.pack(fill="both", expand=True, padx=PADX, pady=(GAP, GAP))

        txt_wrap = tk.Frame(log_frame)
        txt_wrap.pack(fill="both", expand=True)
        vscroll = tk.Scrollbar(txt_wrap, orient="vertical")
        hscroll = tk.Scrollbar(txt_wrap, orient="horizontal")
        self.log_text = tk.Text(
            txt_wrap,
            height=6,
            font=self.f_mono,
            fg="#222",
            wrap="none",
            yscrollcommand=vscroll.set,
            xscrollcommand=hscroll.set,
        )
        vscroll.config(command=self.log_text.yview)
        hscroll.config(command=self.log_text.xview)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        txt_wrap.rowconfigure(0, weight=1)
        txt_wrap.columnconfigure(0, weight=1)

        self._append_log("Lightweight Monitor UI Client 啟動完成。")
        self._append_log(f"連線目標 Core Service: {self.client.host}:{self.client.port}")

    # --------------------------------------------------------------------------
    # Station Cards Dashboard Rendering
    # --------------------------------------------------------------------------
    def _render_station_cards(self) -> None:
        """Render card badges for all configured test stations."""
        for w in self.stations_container.winfo_children():
            w.destroy()
        self._station_widgets.clear()

        for idx, st in enumerate(self._stations):
            st_name = st.get("station", "UNKNOWN")
            dev_id = str(st.get("device", ""))
            status = str(st.get("status", "DISCONNECTED")).upper()

            card = tk.Frame(self.stations_container, relief="ridge", bd=1, padx=8, pady=4)
            card.pack(side="left", fill="both", expand=True, padx=4)

            # Header
            header = tk.Frame(card)
            header.pack(fill="x")
            lbl_title = tk.Label(header, text=f"{st_name} (機台 {dev_id})", font=self.f_bold)
            lbl_title.pack(side="left")

            lbl_badge = tk.Label(
                header,
                text=status,
                font=self.f_bold,
                padx=6,
                pady=1,
                relief="flat",
            )
            lbl_badge.pack(side="right")
            self._apply_badge_style(lbl_badge, status)

            # Body info: the device ID is the only address the operator sees or types.
            lbl_dev = tk.Label(card, text=f"設備: {dev_id}", font=self.f_mono, fg="#555")
            lbl_dev.pack(anchor="w", pady=(2, 4))

            # Select button
            btn_sel = tk.Button(
                card,
                text="選定觀測",
                font=("Arial", 9),
                command=lambda d=dev_id: self._select_device(d),
            )
            btn_sel.pack(fill="x")

            self._station_widgets[dev_id] = {
                "card": card,
                "badge": lbl_badge,
                "device": lbl_dev,
            }

    @staticmethod
    def _apply_badge_style(label: tk.Label, status: str) -> None:
        status_up = status.upper()
        if "BUSY" in status_up or "TESTING" in status_up:
            label.config(text="BUSY (Testing)", bg="#ff9800", fg="white")
        elif "FROZEN" in status_up:
            label.config(text="FROZEN 警示", bg="#f44336", fg="white")
        elif "IDLE" in status_up or "OK" in status_up:
            label.config(text="IDLE 閒置", bg="#4caf50", fg="white")
        else:
            label.config(text="DISCONNECTED", bg="#9e9e9e", fg="white")

    def _station_of(self, device: str) -> str:
        for st in self._stations:
            if str(st.get("device", "")) == device:
                return str(st.get("station", ""))
        return ""

    def _select_device(self, device: str) -> None:
        self.var_sel_device.set(device)
        self.var_sel_station.set(self._station_of(device))
        self._append_log(f"已選定觀測設備: {device} (站別 {self.var_sel_station.get()})")
        # Trigger immediate snapshot for newly selected device
        self.on_capture_snapshot()

    def _on_device_selected(self, _event: Any = None) -> None:
        self.var_sel_station.set(self._station_of(self.var_sel_device.get()))

    # --------------------------------------------------------------------------
    # Status Polling & Health Heartbeat
    # --------------------------------------------------------------------------
    def _poll_status(self) -> None:
        """Background thread query for status to keep UI completely responsive."""
        if self._is_closing:
            return
        # Skipped (no new thread, no new request) while the previous poll is still unanswered.
        self.client.start_status_poll(lambda res: self._ui_queue.put(lambda: self._update_status_ui(res)))
        self.after(1500, self._poll_status)

    def _update_status_ui(self, status_res: Dict[str, Any]) -> None:
        if status_res.get("status") in ("running", "ok"):
            uptime = status_res.get("uptime_seconds", 0)
            active_conns = status_res.get("active_connections", 0)
            busy_devs = status_res.get("busy_devices", [])
            self.lbl_core_status.config(text="● 服務連線正常", fg="#2e7d32")
            self.lbl_core_info.config(
                text=f"Uptime: {uptime}s | 連線數: {active_conns} | 忙碌機台: {busy_devs or '無'}"
            )

            # Update stations list if returned
            stations = status_res.get("stations")
            if stations and isinstance(stations, list):
                self._stations = stations
                device_ids = [str(st.get("device", "")) for st in stations]
                if device_ids != self._rendered_device_ids:
                    self._rendered_device_ids = device_ids
                    self._render_station_cards()
                self.combo_device.config(values=device_ids)
                if self.var_sel_device.get() not in device_ids and device_ids:
                    self._select_device(device_ids[0])
                for st in stations:
                    w = self._station_widgets.get(str(st.get("device", "")))
                    if w:
                        self._apply_badge_style(w["badge"], st.get("status", "DISCONNECTED"))
        else:
            err = status_res.get("message") or status_res.get("error") or "連線中斷"
            self.lbl_core_status.config(text="○ 服務未連線", fg="#c62828")
            self.lbl_core_info.config(text=f"{err}")
            for w in self._station_widgets.values():
                self._apply_badge_style(w["badge"], "DISCONNECTED")

    # --------------------------------------------------------------------------
    # Logging & Threading Queue Plumbing
    # --------------------------------------------------------------------------
    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d(%H:%M:%S)")
        self.log_text.insert("end", f"{ts}: {msg}\n")
        self.log_text.see("end")

    def _drain_queues(self) -> None:
        try:
            while True:
                self._append_log(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    self._append_log(f"UI 回呼錯誤: {exc}")
        except queue.Empty:
            pass
        if not self._is_closing:
            self.after(100, self._drain_queues)

    # --------------------------------------------------------------------------
    # Snapshot & Live Stream Handling
    # --------------------------------------------------------------------------
    def on_open_device_settings(self) -> None:
        """Open the device list settings page (works without a running Core Service)."""
        if DeviceSettingsWindow is None:
            self._append_log("設備設定頁模組不可用。")
            return
        existing = getattr(self, "_settings_window", None)
        if existing is not None:
            try:
                existing.lift()
                return
            except tk.TclError:
                self._settings_window = None
        self._settings_window = DeviceSettingsWindow(self, client=self.client, restart_core=self._restart_core_daemon)

    def _restart_core_daemon(self) -> Dict[str, Any]:
        """Stop and start Core Service on this machine; the settings page checks for busy devices first."""
        from core_daemon import restart_core_daemon

        try:
            res = restart_core_daemon(host=self.client.host, port=self.client.port, timeout=10.0)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        ok = res.get("status") in ("running", "ok")
        return {"status": "ok" if ok else "error", "message": res.get("message", res)}

    def on_toggle_connect(self) -> None:
        """Parse core service address and reconnect."""
        addr_text = self.e_core_addr.get().strip()
        host = "127.0.0.1"
        port = 5000
        if ":" in addr_text:
            parts = addr_text.split(":", 1)
            host = parts[0].strip() or "127.0.0.1"
            if parts[1].strip().isdigit():
                port = int(parts[1].strip())
        self.client.set_target(host, port)
        self._append_log(f"更新目標連線位址至 {host}:{port}，嘗試連線...")
        self.client.disconnect()
        threading.Thread(
            target=lambda: self.client.connect(timeout=2.0), daemon=True
        ).start()

    def on_capture_snapshot(self) -> None:
        """Request a single snapshot frame from Core Service."""
        device = self.var_sel_device.get().strip()
        if not device:
            self._append_log("尚未選定設備，無法擷取快照。")
            return
        self._append_log(f"向 Core Service 請求快照 (設備 {device})...")

        def worker() -> None:
            res = self.client.get_snapshot(device=device)
            self._ui_queue.put(lambda: self._display_snapshot_result(res))

        threading.Thread(target=worker, daemon=True).start()

    def _display_snapshot_result(self, res: Dict[str, Any]) -> None:
        if res.get("status") == "ok":
            b64_data = res.get("image_base64", "")
            w = res.get("width", 0)
            h = res.get("height", 0)

            if b64_data:
                try:
                    img_bytes = base64.b64decode(b64_data)
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    self._latest_pil_image = pil_img
                    self._render_image_on_canvas(pil_img)
                    self.lbl_preview_info.config(
                        text=f"解析度: {w}x{h} | 時間: {time.strftime('%H:%M:%S')}"
                    )
                    self._append_log(f"快照接收成功 ({w}x{h})")
                except Exception as exc:
                    self._append_log(f"解碼快照影像失敗: {exc}")
            else:
                self._append_log("快照資料為空")
        else:
            err = res.get("message") or res.get("error") or "未知錯誤"
            self._append_log(f"擷取快照失敗: {err}")
            self.lbl_preview_info.config(text=f"快照錯誤: {err}")

    def _render_image_on_canvas(self, pil_img: Image.Image) -> None:
        """Fit and render PIL image onto canvas with letterbox scaling."""
        canv_w = self.canvas_preview.winfo_width()
        canv_h = self.canvas_preview.winfo_height()
        if canv_w <= 1 or canv_h <= 1:
            canv_w, canv_h = 480, 270

        orig_w, orig_h = pil_img.size
        scale = min(canv_w / orig_w, canv_h / orig_h)
        new_w = max(1, int(orig_w * scale))
        new_h = max(1, int(orig_h * scale))

        resized = pil_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
        self._latest_tk_photo = ImageTk.PhotoImage(resized)

        self.canvas_preview.delete("all")
        x_center = canv_w // 2
        y_center = canv_h // 2
        self.canvas_preview.create_image(x_center, y_center, image=self._latest_tk_photo)

    def on_toggle_live_stream(self) -> None:
        """Start or stop the background live polling stream."""
        if self._streaming_active:
            self._streaming_active = False
            self.btn_live_stream.config(text="▶ 開始串流")
            self._append_log("已停止即時串流監控。")
        else:
            self._streaming_active = True
            self.btn_live_stream.config(text="⏹ 停止串流")
            self._append_log("開始即時串流監控 (頻率: ~3 fps)...")
            # A still-running loop picks the flag back up; never stack a second one.
            if self._stream_thread is None or not self._stream_thread.is_alive():
                self._stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
                self._stream_thread.start()

    def _stream_worker(self) -> None:
        """Periodic snapshot requester loop."""
        while self._streaming_active and not self._is_closing:
            device = self.var_sel_device.get().strip()
            if not device:
                time.sleep(0.5)
                continue

            res = self.client.get_snapshot(
                device=device,
                quality=65,
                timeout=3.0,
            )
            if res.get("status") == "ok":
                b64_data = res.get("image_base64")
                if b64_data:
                    try:
                        img_bytes = base64.b64decode(b64_data)
                        pil_img = Image.open(io.BytesIO(img_bytes))
                        self._latest_pil_image = pil_img
                        w = res.get("width", pil_img.width)
                        h = res.get("height", pil_img.height)

                        def update_ui(img=pil_img, info=f"解析度: {w}x{h} | 時間: {time.strftime('%H:%M:%S')}"):
                            self._render_image_on_canvas(img)
                            self.lbl_preview_info.config(text=info)

                        self._ui_queue.put(update_ui)
                    except Exception:
                        pass
            time.sleep(0.3)

    def on_open_streamer_window(self) -> None:
        """Open a standalone StreamerWindow using current snapshot stream getter."""
        if StreamerWindow is None:
            self._append_log("StreamerWindow 模組不可用。")
            return
        if self._streamer_window is not None:
            try:
                self._streamer_window.lift()
                return
            except Exception:
                self._streamer_window = None

        def frame_getter() -> Any:
            if self._latest_pil_image is not None and _CV2_OK:
                import numpy as np
                return cv2.cvtColor(np.array(self._latest_pil_image), cv2.COLOR_RGB2BGR)
            return None

        def on_close() -> None:
            self._streamer_window = None
            self._append_log("已關閉獨立串流視窗。")

        self._streamer_window = StreamerWindow(
            self,
            frame_getter=frame_getter,
            input_sender=None,  # Readonly monitoring
            fps=15,
            title=f"KVM Streamer - {self.var_sel_device.get()}",
            on_close=on_close,
        )
        self._append_log("已開啟獨立串流視窗。")

    # --------------------------------------------------------------------------
    # Manual Diagnostics & Pattern Creation
    # --------------------------------------------------------------------------
    def on_run_manual_check(self) -> None:
        """Trigger a manual check command over TCP."""
        device = self.var_sel_device.get().strip()
        if not device:
            self._append_log("尚未選定設備，無法執行手動檢查。")
            return
        try:
            timeout_sec = float(self.var_timeout.get().strip())
        except ValueError:
            timeout_sec = 5.0

        self._append_log(f"手動送出 inspect 指令: Device={device}, Timeout={timeout_sec}s...")
        self.btn_run_check.config(state="disabled", text="⏳ 檢查執行中...")
        self.lbl_check_result.config(text="診斷結果: 執行中...", fg="blue")

        def worker() -> None:
            res = self.client.manual_check(
                device=device,
                timeout_sec=timeout_sec,
            )
            self._ui_queue.put(lambda: self._display_check_result(res))

        threading.Thread(target=worker, daemon=True).start()

    def _display_check_result(self, res: Dict[str, Any]) -> None:
        self.btn_run_check.config(state="normal", text="⚡ 執行手動診斷檢查")
        text = format_manual_check(res)
        status = res.get("status", "error")
        if status == "ok":
            fg = "#2e7d32" if res.get("active") else "#555"
        elif status == "timeout":
            fg = "#d84315"
        elif res.get("error") == "device_busy":
            fg = "#e65100"
        else:
            fg = "#b71c1c"
        self.lbl_check_result.config(text=text, fg=fg)
        self._append_log(text)

    def on_save_capture(self) -> None:
        """Save latest snapshot frame to template capture path."""
        if self._latest_pil_image is None:
            self._append_log("尚未擷取影格，請先點擊『📷 擷取快照』。")
            return
        save_path = self._templates.capture_path()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._latest_pil_image.save(save_path)
            self._append_log(f"已儲存影格至模板目錄: {save_path}")
        except Exception as exc:
            self._append_log(f"儲存影格失敗: {exc}")

    def on_create_pattern(self) -> None:
        """Open pattern cropper with latest captured frame."""
        if PatternCropper is None:
            self._append_log("PatternCropper 模組不可用。")
            return
        src = self._templates.capture_path()
        if not src.exists():
            if self._latest_pil_image:
                self.on_save_capture()
            else:
                self._append_log("請先按『擷取快照』並儲存影格。")
                return
        PatternCropper(
            self,
            self._templates,
            str(src),
            device=self.var_sel_station.get().strip().upper(),
            template_key="window",
            on_saved=self._append_log,
        )

    def on_show_match_results(self) -> None:
        """Display MatchResultsWindow."""
        device = self.var_sel_station.get().strip().upper()
        if device not in self._templates.devices():
            device = "FCT"
        MatchResultsWindow(self, self._templates, device)

    # --------------------------------------------------------------------------
    # Lifecycle & Process Isolation
    # --------------------------------------------------------------------------
    def _on_close(self) -> None:
        """Gracefully close UI process without affecting Core Service or LabVIEW."""
        self._is_closing = True
        self._streaming_active = False

        if self._streamer_window:
            try:
                self._streamer_window.destroy()
            except Exception:
                pass

        # Disconnect TCP client cleanly
        try:
            self.client.disconnect()
        except Exception:
            pass

        self.destroy()


# ==============================================================================
# Main Entry Point
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas2 Lightweight Monitor Client")
    parser.add_argument("--host", default="127.0.0.1", help="Core Service TCP host")
    parser.add_argument("--port", type=int, default=5000, help="Core Service TCP port")
    parser.add_argument(
        "--headless-check",
        action="store_true",
        help="Perform a quick TCP ping/status check and exit without GUI",
    )
    args = parser.parse_args()

    if args.headless_check:
        client = CoreServiceClient(host=args.host, port=args.port)
        status = client.get_status(timeout=3.0)
        print(json.dumps(status, indent=2))
        sys.exit(0 if status.get("status") in ("running", "ok") else 1)

    app = AtlasUI(core_host=args.host, core_port=args.port)
    app.mainloop()


if __name__ == "__main__":
    main()
