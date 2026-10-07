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
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

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

logger = logging.getLogger("MonitorUI")


# ==============================================================================
# CoreServiceClient: Thread-Safe TCP Client to Core Service
# ==============================================================================

class CoreServiceClient:
    """Thread-safe TCP JSON client that interacts with the headless Core Service."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5000) -> None:
        self.host = host
        self.port = int(port)
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None

    def set_target(self, host: str, port: int) -> None:
        """Update target host and port."""
        with self._lock:
            p = int(port)
            if self.host != host or self.port != p:
                self._disconnect_locked()
                self.host = host
                self.port = p

    def is_connected(self) -> bool:
        """Check if socket appears connected."""
        with self._lock:
            return self._sock is not None

    def connect(self, timeout: float = 2.0) -> bool:
        """Explicitly attempt to establish a TCP connection."""
        with self._lock:
            if self._sock is not None:
                return True
            try:
                s = socket.create_connection((self.host, self.port), timeout=timeout)
                s.settimeout(timeout)
                self._sock = s
                return True
            except Exception:
                self._sock = None
                return False

    def _disconnect_locked(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def disconnect(self) -> None:
        """Gracefully disconnect from the Core Service."""
        with self._lock:
            self._disconnect_locked()

    def send_request(
        self,
        cmd: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Send a newline-delimited JSON command and read the parsed JSON reply."""
        req_id = f"ui-{uuid.uuid4().hex[:8]}"
        req: Dict[str, Any] = {"req_id": req_id, "cmd": cmd}
        if payload:
            req.update(payload)

        data = (json.dumps(req) + "\n").encode("utf-8")

        with self._lock:
            # Reconnect if not currently connected
            if self._sock is None:
                try:
                    s = socket.create_connection((self.host, self.port), timeout=min(timeout, 2.0))
                    self._sock = s
                except Exception as exc:
                    return {
                        "req_id": req_id,
                        "status": "error",
                        "error": "connection_failed",
                        "message": f"Cannot connect to Core Service at {self.host}:{self.port}: {exc}",
                    }

            try:
                self._sock.settimeout(timeout)
                self._sock.sendall(data)

                buf = bytearray()
                while b"\n" not in buf:
                    chunk = self._sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("Core Service closed connection")
                    buf.extend(chunk)

                line = buf.split(b"\n")[0]
                return json.loads(line.decode("utf-8"))
            except Exception as exc:
                self._disconnect_locked()
                return {
                    "req_id": req_id,
                    "status": "error",
                    "error": "communication_error",
                    "message": f"Communication error with Core Service: {exc}",
                }

    def ping(self, timeout: float = 2.0) -> bool:
        """Send a ping check."""
        resp = self.send_request("ping", timeout=timeout)
        return resp.get("status") == "ok"

    def get_status(self, timeout: float = 2.0) -> Dict[str, Any]:
        """Query real-time Core Service status."""
        res = self.send_request("status", timeout=timeout)
        if res.get("status") == "ok" and isinstance(res.get("data"), dict):
            merged = dict(res["data"])
            merged["req_id"] = res.get("req_id")
            if "status" not in merged:
                merged["status"] = "running"
            return merged
        return res

    def get_stations(self, timeout: float = 2.0) -> Dict[str, Any]:
        """Query configured stations status."""
        res = self.send_request("stations", timeout=timeout)
        if res.get("status") == "ok" and isinstance(res.get("data"), dict):
            merged = dict(res["data"])
            merged["req_id"] = res.get("req_id")
            if "status" not in merged:
                merged["status"] = "ok"
            return merged
        return res

    def get_snapshot(
        self,
        kvm_ip: Optional[str] = None,
        station: Optional[str] = None,
        device: Optional[str] = None,
        save_path: Optional[Union[str, Path]] = None,
        format: str = "jpeg",
        quality: int = 80,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Request a snapshot image without acquiring device locks."""
        payload: Dict[str, Any] = {
            "format": format,
            "quality": quality,
            "read_only": True,
            "no_lock": True,
        }
        if kvm_ip:
            payload["kvm_ip"] = kvm_ip
        if station:
            payload["station"] = station
        if device:
            payload["device"] = device
        if save_path:
            payload["save_path"] = str(save_path)
        return self.send_request("snapshot", payload, timeout=timeout)

    def manual_check(
        self,
        station: str,
        device: str,
        kvm_ip: Optional[str] = None,
        timeout_sec: float = 5.0,
    ) -> Dict[str, Any]:
        """Trigger a manual visual check command through Core Service."""
        payload: Dict[str, Any] = {
            "station": station,
            "device": device,
            "timeout_sec": timeout_sec,
        }
        if kvm_ip:
            payload["kvm_ip"] = kvm_ip
        return self.send_request("check", payload, timeout=timeout_sec + 4.0)


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
        self._stations: List[Dict[str, Any]] = [
            {"station": "DFU", "device": "1", "kvm_ip": "192.168.132.70", "status": "DISCONNECTED"},
            {"station": "FCT", "device": "1", "kvm_ip": "192.168.132.71", "status": "DISCONNECTED"},
            {"station": "BT", "device": "1", "kvm_ip": "192.168.132.72", "status": "DISCONNECTED"},
        ]
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
        tk.Label(row_target, text="目標機台:", font=self.f_label).pack(side="left")
        self.var_sel_station = tk.StringVar(value="DFU")
        self.combo_station = ttk.Combobox(
            row_target,
            textvariable=self.var_sel_station,
            values=["DFU", "FCT", "BT"],
            state="readonly",
            width=6,
        )
        self.combo_station.pack(side="left", padx=4)
        self.combo_station.bind("<<ComboboxSelected>>", self._on_station_selected)

        tk.Label(row_target, text="編號:", font=self.f_label).pack(side="left", padx=(8, 0))
        self.var_sel_device = tk.StringVar(value="1")
        self.e_sel_device = tk.Entry(row_target, textvariable=self.var_sel_device, width=4, justify="center", font=self.f_entry)
        self.e_sel_device.pack(side="left", padx=4)

        # Target KVM IP
        row_ip = tk.Frame(ctrl_frame)
        row_ip.pack(fill="x", pady=4)
        tk.Label(row_ip, text="KVM IP:", font=self.f_label).pack(side="left")
        self.var_sel_kvm_ip = tk.StringVar(value="192.168.132.70")
        self.e_sel_kvm_ip = tk.Entry(row_ip, textvariable=self.var_sel_kvm_ip, width=16, font=self.f_entry)
        self.e_sel_kvm_ip.pack(side="left", padx=4)

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
            dev_id = str(st.get("device", "1"))
            ip = str(st.get("kvm_ip", ""))
            status = st.get("status", "DISCONNECTED").upper()

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

            # Body info
            lbl_ip = tk.Label(card, text=f"IP: {ip}", font=self.f_mono, fg="#555")
            lbl_ip.pack(anchor="w", pady=(2, 4))

            # Select button
            btn_sel = tk.Button(
                card,
                text="選定觀測",
                font=("Arial", 9),
                command=lambda s=st_name, d=dev_id, k=ip: self._select_station(s, d, k),
            )
            btn_sel.pack(fill="x")

            self._station_widgets[st_name] = {
                "card": card,
                "badge": lbl_badge,
                "ip": lbl_ip,
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

    def _select_station(self, station: str, device: str, kvm_ip: str) -> None:
        self.var_sel_station.set(station)
        self.var_sel_device.set(device)
        self.var_sel_kvm_ip.set(kvm_ip)
        self._append_log(f"已選定觀測機台: {station} (機台 {device}, IP: {kvm_ip})")
        # Trigger immediate snapshot for newly selected station
        self.on_capture_snapshot()

    def _on_station_selected(self, _event: Any = None) -> None:
        st_name = self.var_sel_station.get()
        for st in self._stations:
            if st.get("station") == st_name:
                self.var_sel_device.set(str(st.get("device", "1")))
                self.var_sel_kvm_ip.set(str(st.get("kvm_ip", "")))
                break

    # --------------------------------------------------------------------------
    # Status Polling & Health Heartbeat
    # --------------------------------------------------------------------------
    def _poll_status(self) -> None:
        """Background thread query for status to keep UI completely responsive."""
        if self._is_closing:
            return

        def worker() -> None:
            try:
                res = self.client.get_status(timeout=2.0)
                self._ui_queue.put(lambda: self._update_status_ui(res))
            except Exception as exc:
                self._ui_queue.put(lambda: self._update_status_ui({"status": "error", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()
        if not self._is_closing:
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
                for st in stations:
                    st_name = st.get("station")
                    status = st.get("status", "DISCONNECTED")
                    w = self._station_widgets.get(st_name)
                    if w:
                        self._apply_badge_style(w["badge"], status)
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
        kvm_ip = self.var_sel_kvm_ip.get().strip()
        station = self.var_sel_station.get().strip()
        device = self.var_sel_device.get().strip()
        self._append_log(f"向 Core Service 請求快照 ({station}:{device}, IP: {kvm_ip})...")

        def worker() -> None:
            res = self.client.get_snapshot(kvm_ip=kvm_ip, station=station, device=device)
            self._ui_queue.put(lambda: self._display_snapshot_result(res))

        threading.Thread(target=worker, daemon=True).start()

    def _display_snapshot_result(self, res: Dict[str, Any]) -> None:
        if res.get("status") == "ok":
            b64_data = res.get("image_base64", "")
            w = res.get("width", 0)
            h = res.get("height", 0)
            pts = res.get("pts")
            pts_str = f"PTS: {pts:.4f}" if pts is not None else "PTS: n/a"

            if b64_data:
                try:
                    img_bytes = base64.b64decode(b64_data)
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    self._latest_pil_image = pil_img
                    self._render_image_on_canvas(pil_img)
                    self.lbl_preview_info.config(
                        text=f"解析度: {w}x{h} | {pts_str} | 時間: {time.strftime('%H:%M:%S')}"
                    )
                    self._append_log(f"快照接收成功 ({w}x{h}, {pts_str})")
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
            self._stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
            self._stream_thread.start()

    def _stream_worker(self) -> None:
        """Periodic snapshot requester loop."""
        while self._streaming_active and not self._is_closing:
            kvm_ip = self.var_sel_kvm_ip.get().strip()
            station = self.var_sel_station.get().strip()
            device = self.var_sel_device.get().strip()

            res = self.client.get_snapshot(
                kvm_ip=kvm_ip,
                station=station,
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
                        pts = res.get("pts")
                        pts_str = f"PTS: {pts:.4f}" if pts is not None else "PTS: n/a"

                        def update_ui(img=pil_img, info=f"解析度: {w}x{h} | {pts_str} | 時間: {time.strftime('%H:%M:%S')}"):
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
            title=f"KVM Streamer - {self.var_sel_station.get()}:{self.var_sel_device.get()}",
            on_close=on_close,
        )
        self._append_log("已開啟獨立串流視窗。")

    # --------------------------------------------------------------------------
    # Manual Diagnostics & Pattern Creation
    # --------------------------------------------------------------------------
    def on_run_manual_check(self) -> None:
        """Trigger a manual check command over TCP."""
        station = self.var_sel_station.get().strip().upper()
        device = self.var_sel_device.get().strip()
        kvm_ip = self.var_sel_kvm_ip.get().strip()
        try:
            timeout_sec = float(self.var_timeout.get().strip())
        except ValueError:
            timeout_sec = 5.0

        self._append_log(f"手動送出 check 指令: Station={station}, Device={device}, KVM={kvm_ip}, Timeout={timeout_sec}s...")
        self.btn_run_check.config(state="disabled", text="⏳ 檢查執行中...")
        self.lbl_check_result.config(text="診斷結果: 執行中...", fg="blue")

        def worker() -> None:
            res = self.client.manual_check(
                station=station,
                device=device,
                kvm_ip=kvm_ip,
                timeout_sec=timeout_sec,
            )
            self._ui_queue.put(lambda: self._display_check_result(res))

        threading.Thread(target=worker, daemon=True).start()

    def _display_check_result(self, res: Dict[str, Any]) -> None:
        self.btn_run_check.config(state="normal", text="⚡ 執行手動診斷檢查")
        status = res.get("status", "error")
        result_val = res.get("result", "NONE")
        elapsed = res.get("elapsed_sec", 0.0)

        if status == "ok":
            color = "#2e7d32" if result_val == "PASS" else "#c62828"
            self.lbl_check_result.config(
                text=f"診斷結果: {result_val} (耗時: {elapsed}s)", fg=color
            )
            self._append_log(f"診斷檢查成功: {result_val} (耗時 {elapsed}s)")
        elif status == "busy":
            self.lbl_check_result.config(text="診斷結果: BUSY (設備忙碌中/LabVIEW測試中)", fg="#e65100")
            self._append_log("診斷檢查拒絕: 設備當前處於 BUSY 狀態 (LabVIEW 正在測試)。")
        elif status == "timeout":
            self.lbl_check_result.config(text=f"診斷結果: TIMEOUT (耗時 {elapsed}s)", fg="#d84315")
            self._append_log(f"診斷檢查逾時: {elapsed}s 未命中條件。")
        else:
            err = res.get("message") or res.get("error") or "未知錯誤"
            self.lbl_check_result.config(text=f"診斷錯誤: {err}", fg="#b71c1c")
            self._append_log(f"診斷檢查失敗: {err}")

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
