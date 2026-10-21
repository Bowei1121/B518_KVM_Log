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
import datetime
import io
import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

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
from core_protocol import flatten_reply
from match_diagnostics import MatchDiagnostics, note_for_language
from template_catalog import TemplateCatalog
from ui_logfile import DailyLogFile, log_dir_for_template_root
import ui_i18n
from ui_i18n import bind, pair, t
from ui_widgets import (
    BUTTON_BG,
    ENTRY_BG,
    ERROR_FG,
    INFO_FG,
    LOG_BG,
    LOG_ERROR_FG,
    LOG_FG,
    LOG_WARN_FG,
    MUTED_FG,
    OK_FG,
    PANEL_BG,
    PREVIEW_BG,
    TEXT_FG,
    WARN_FG,
    WINDOW_BG,
    StatusLight,
    Tooltip,
    apply_light_ttk_style,
    pin_light_appearance,
)

_PATTERN_IMPORT_ERROR: Optional[BaseException] = None
try:
    from pattern_tools import PatternCropper
except Exception as exc:
    PatternCropper = None  # type: ignore[assignment,misc]
    _PATTERN_IMPORT_ERROR = exc


def format_exception_text(exc: BaseException) -> str:
    """Full traceback text for the Log (the HMI only ever shows a short message)."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()

try:
    from stream_view import StreamerWindow
except Exception:
    StreamerWindow = None  # type: ignore[assignment,misc]

try:
    from device_settings_window import DeviceSettingsWindow
except Exception:
    DeviceSettingsWindow = None  # type: ignore[assignment,misc]

logger = logging.getLogger("MonitorUI")

STATION_ORDER = ("DFU", "FCT", "BT")
CARDS_PER_ROW = 3

_STATE_KEYS = {
    "standby": "state_standby",
    "preparing": "state_preparing",
    "monitoring": "state_monitoring",
    "review": "state_review",
    "complete": "state_complete",
    "manual_stop_complete": "state_manual_stop_complete",
}

_EVIDENCE_KEYS = {
    "testing": "evidence_testing",
    "result": "evidence_result",
}


# ==============================================================================
# Bilingual text helpers (pure functions: they take the language explicitly)
# ==============================================================================

def _bilingual(key: str, **kwargs: Any) -> Tuple[str, str]:
    """``(English, 中文)`` for ``key``. A tuple argument is a ``(en, zh)`` pair and is split per language."""
    en_kwargs = {name: (value[0] if isinstance(value, tuple) else value) for name, value in kwargs.items()}
    zh_kwargs = {name: (value[1] if isinstance(value, tuple) else value) for name, value in kwargs.items()}
    return t(key, lang="en", **en_kwargs), t(key, lang="zh", **zh_kwargs)


def format_manual_check(res: Dict[str, Any], lang: Optional[str] = None) -> str:
    """Operator text for one inspect reply: the started channels and the Log state, in ``lang``."""
    status = res.get("status", "error")
    elapsed = res.get("elapsed_sec", 0.0)
    if status == "ok":
        state = res.get("state") or ""
        if state in _STATE_KEYS:
            state_name = t(_STATE_KEYS[state], lang)
        else:
            state_name = state or t("state_unknown", lang)
        channels = []
        for row in res.get("active") or []:
            evidence = row.get("evidence", "")
            evidence = t(_EVIDENCE_KEYS[evidence], lang) if evidence in _EVIDENCE_KEYS else evidence
            channels.append(t("res_channel", lang, slot=row.get("slot"), evidence=evidence))
        text = t("res_ok", lang, state=state_name)
        if channels:
            text = t("res_with_channels", lang, result=text, channels=t("sep_list", lang).join(channels))
        if elapsed:
            text += t("res_elapsed", lang, elapsed=elapsed)
        return text
    if status == "timeout":
        return t("res_timeout", lang, elapsed=elapsed)
    if res.get("error") == "device_busy":
        return t("res_busy", lang)
    err = res.get("message") or res.get("error") or t("err_unknown", lang)
    return t("res_error", lang, error=err)


def manual_check_error_key(res: Dict[str, Any]) -> str:
    """Short HMI text key for a failed inspect reply. The full text goes to the Log."""
    if res.get("status") == "timeout":
        return "err_timeout"
    if res.get("error") == "device_busy":
        return "err_busy"
    return "err_check"


def format_reading_metrics(station: Dict[str, Any], lang: Optional[str] = None) -> str:
    """Three lines for one station card (issue #94): frame size and scale, last reading, readable ratio."""
    size = station.get("frame_size")
    frame = f"{size[0]}×{size[1]}" if size else "—"
    scale = station.get("picture_scale")
    cell = station.get("cell_px")
    last = station.get("last_reading")
    ratio = station.get("readable_ratio") or {}
    lines = [t(
        "rd_frame", lang,
        frame=frame,
        scale="—" if scale is None else f"{scale:.3f}",
        cell="—" if cell is None else f"{cell:.1f} px",
    )]
    if last:
        reason = last.get("reason") or t("rd_reason_default", lang)
        lines.append(t("rd_last", lang, reason=reason, age=float(last.get("age_sec", 0))))
    else:
        lines.append(t("rd_last_none", lang))
    samples = int(ratio.get("samples") or 0)
    if samples and ratio.get("ratio") is not None:
        lines.append(t("rd_ratio", lang, ratio=f"{ratio['ratio'] * 100:.0f}", samples=samples))
    else:
        lines.append(t("rd_ratio_none", lang))
    return "\n".join(lines)


# ==============================================================================
# CoreServiceClient: Thread-Safe TCP Client to Core Service
# ==============================================================================

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
        return flatten_reply(self.send_request("core_status", timeout=timeout))

    def get_stations(self, timeout: float = 2.0) -> Dict[str, Any]:
        """Query configured stations status. Same core_status reply as get_status."""
        return self.get_status(timeout)

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
        return flatten_reply(self.send_request("snapshot", payload, timeout=timeout))

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
        return flatten_reply(self.send_request("inspect", payload, timeout=timeout_sec + 4.0))


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
        bind(self, "match_title", option="title")
        self.geometry("980x700")
        self.configure(bg=WINDOW_BG)
        pin_light_appearance(self)
        self.photo: Optional[ImageTk.PhotoImage] = None
        self.summary: Optional[Dict[str, Any]] = None
        self.records: List[Dict[str, Any]] = []

        top = tk.Frame(self, padx=8, pady=8, bg=WINDOW_BG)
        top.pack(fill="x")
        bind(tk.Label(top, bg=WINDOW_BG, fg=TEXT_FG), "match_device").pack(side="left")
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
        Tooltip(self.device_box, lambda: t("tip_match_device"))
        self.status_var = tk.StringVar()
        tk.Label(top, textvariable=self.status_var, bg=WINDOW_BG, fg=TEXT_FG).pack(side="left", padx=12)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = tk.Frame(body, width=320, bg=WINDOW_BG)
        right = tk.Frame(body, bg=WINDOW_BG)
        body.add(left, weight=1)
        body.add(right, weight=3)
        self.listbox = tk.Listbox(left, font=("Arial", 11), exportselection=False, bg=PANEL_BG, fg=TEXT_FG)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._select)
        Tooltip(self.listbox, lambda: t("match_tip_list"))
        self.image_label = tk.Label(
            right, text=t("match_pick"), anchor="center", bg=WINDOW_BG, fg=TEXT_FG
        )
        self.image_label.pack(fill="both", expand=True)
        self.load()
        ui_i18n.on_language_change(self._relabel)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _relabel(self) -> None:
        """Language switch: rebuild the status and the rows in the new language, keeping the chosen row."""
        if not self.winfo_exists():
            return
        picked = self.listbox.curselection()
        self.load()
        if picked and picked[0] < len(self.records):
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(picked[0])
            self._select()

    def _on_destroy(self, event: Any) -> None:
        if event.widget is self:
            ui_i18n.off_language_change(self._relabel)

    def load(self) -> None:
        self.summary = MatchDiagnostics.load(self.catalog.root, self.device_var.get())
        self.records = [] if self.summary is None else self.summary.get("records", [])
        self.listbox.delete(0, "end")
        if not self.summary:
            self.status_var.set(t("match_none"))
            self.image_label.configure(text=t("match_none_hint"), image="")
            return
        operation = self.summary.get("operation", "")
        state = t("match_done") if self.summary.get("ok") else t("match_failed")
        self.status_var.set(t("match_summary", operation=operation, state=state, count=len(self.records)))
        for record in self.records:
            score = record.get("score")
            score_text = "n/a" if score is None else f"{score:.3f}"
            status = t("match_hit") if record.get("matched") else t("match_miss")
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
            # Notes written before #106 are plain text; newer ones hold both languages.
            self.image_label.configure(
                text=note_for_language(record.get("note"), ui_i18n.current_language()) or t("match_no_image"),
                image="",
            )
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
            self.image_label.configure(text=t("match_read_fail", error=exc), image="")


# ==============================================================================
# AtlasUI: Decoupled Lightweight Monitor Client
# ==============================================================================

class AtlasUI(_BaseTk):
    """Lightweight Monitor Client GUI."""

    def __init__(self, core_host: str = "127.0.0.1", core_port: int = 5000) -> None:
        if not _TK_OK:
            raise RuntimeError("Tkinter is not available in current environment.")
        super().__init__()
        self._tk_scaling = apply_windows_tk_scaling(self)
        ui_i18n.set_language(ui_i18n.load_saved_language())
        bind(self, "win_title", option="title")
        self.configure(bg=WINDOW_BG)
        apply_light_ttk_style(self)
        self.update_idletasks()  # Tk needs the root realised before its appearance can be set
        pin_light_appearance(self)

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
        self.f_icon = (fam, 16)  # icon-only buttons: about 16 pt
        self.f_gear = (fam, 20, "bold")  # ⚙ is the one icon that must read at a glance
        self.f_mono = (mono, 10)
        self.f_large_bold = (fam, 12, "bold")

        # State & Client
        self.client = CoreServiceClient(host=core_host, port=core_port)
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._ui_queue: queue.Queue[Any] = queue.Queue()
        self._templates = TemplateCatalog()
        self._file_log = DailyLogFile(log_dir_for_template_root(self._templates.root), "monitor_ui")
        self._log_seq = 0

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

        # Core Service state: None until the first poll answers; True/False after that.
        self._service_ok: Optional[bool] = None
        self._service_info: Dict[str, Any] = {}

        # Polling
        self._is_closing = False

        self._build_widgets()
        ui_i18n.on_language_change(self._on_language_changed)

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
        # Top-right: language switch (🌐)
        # ----------------------------------------------------------------------
        header = tk.Frame(self, bg=WINDOW_BG)
        header.pack(fill="x", padx=PADX, pady=(GAP, 0))
        self.btn_lang = tk.Button(
            header, text="🌐", font=self.f_icon, bg=BUTTON_BG, fg=TEXT_FG, command=self.on_toggle_language,
        )
        self.btn_lang.pack(side="right")
        Tooltip(self.btn_lang, lambda: t("tip_lang"))

        # ----------------------------------------------------------------------
        # Top Bar: Core Service Connection
        # ----------------------------------------------------------------------
        top_frame = tk.LabelFrame(self, font=self.f_bold, padx=8, pady=6, bg=WINDOW_BG, fg=TEXT_FG)
        bind(top_frame, "top_title")
        top_frame.pack(fill="x", padx=PADX, pady=(GAP, GAP))

        lbl_addr = tk.Label(top_frame, font=self.f_label, bg=WINDOW_BG, fg=TEXT_FG)
        bind(lbl_addr, "lbl_addr")
        lbl_addr.pack(side="left")
        self.e_core_addr = tk.Entry(
            top_frame, font=self.f_entry, width=18, justify="center", bg=ENTRY_BG, fg=TEXT_FG
        )
        self.e_core_addr.insert(0, f"{self.client.host}:{self.client.port}")
        self.e_core_addr.pack(side="left", padx=6)
        Tooltip(self.e_core_addr, lambda: t("tip_addr"))

        self.btn_connect = tk.Button(
            top_frame, text="🔄", font=self.f_icon, bg=BUTTON_BG, fg=TEXT_FG, command=self.on_toggle_connect
        )
        self.btn_connect.pack(side="left", padx=6)
        Tooltip(self.btn_connect, lambda: t("tip_reconnect"))

        self.btn_device_settings = tk.Button(
            top_frame, text="⚙", font=self.f_gear, bg=BUTTON_BG, fg=TEXT_FG,
            command=self.on_open_device_settings,
        )
        self.btn_device_settings.pack(side="left", padx=6)
        Tooltip(self.btn_device_settings, lambda: t("tip_settings"))

        # One lamp for the Core Service: green = connected, red = not connected.
        # Uptime and connection count are in its hover tip.
        self.light_core = StatusLight(top_frame, bg=WINDOW_BG)
        self.light_core.pack(side="left", padx=(12, 2))
        self.light_core.set_light("red")
        self.lbl_core_caption = tk.Label(top_frame, font=self.f_bold, bg=WINDOW_BG, fg=TEXT_FG)
        bind(self.lbl_core_caption, "core_caption")
        self.lbl_core_caption.pack(side="left")
        self._core_tip = Tooltip(self.light_core, self._core_tooltip_text)
        Tooltip(self.lbl_core_caption, self._core_tooltip_text)

        # ----------------------------------------------------------------------
        # Station Real-Time Status Dashboard
        # ----------------------------------------------------------------------
        dash_frame = tk.LabelFrame(self, font=self.f_bold, padx=8, pady=6, bg=WINDOW_BG, fg=TEXT_FG)
        bind(dash_frame, "dash_title")
        dash_frame.pack(fill="x", padx=PADX, pady=GAP)

        self.stations_container = tk.Frame(dash_frame, bg=WINDOW_BG)
        self.stations_container.pack(fill="x", expand=True)

        self._render_station_cards()

        # ----------------------------------------------------------------------
        # Middle: Split into Live Stream View & Controls
        # ----------------------------------------------------------------------
        mid_paned = ttk.Panedwindow(self, orient="horizontal")
        mid_paned.pack(fill="both", expand=True, padx=PADX, pady=GAP)

        # Left pane: Live Monitor Display
        view_frame = tk.LabelFrame(mid_paned, font=self.f_bold, padx=6, pady=6, bg=WINDOW_BG, fg=TEXT_FG)
        bind(view_frame, "view_title")
        mid_paned.add(view_frame, weight=3)

        self.canvas_preview = tk.Canvas(
            view_frame, bg=PREVIEW_BG, width=480, height=270, highlightthickness=0
        )
        self.canvas_preview.pack(fill="both", expand=True, padx=4, pady=4)

        view_bottom = tk.Frame(view_frame, bg=WINDOW_BG)
        view_bottom.pack(fill="x", pady=(4, 0))

        self.lbl_preview_info = tk.Label(
            view_bottom, text=t("preview_none"), font=self.f_mono, fg=MUTED_FG, bg=WINDOW_BG, anchor="w"
        )
        self.lbl_preview_info.pack(side="left", fill="x", expand=True)
        bind(self.lbl_preview_info, "preview_none")

        self.btn_snapshot = tk.Button(
            view_bottom, text="📷", font=self.f_icon, bg=BUTTON_BG, fg=TEXT_FG,
            command=self.on_capture_snapshot,
        )
        self.btn_snapshot.pack(side="right", padx=4)
        Tooltip(self.btn_snapshot, lambda: t("tip_snapshot"))

        self.btn_live_stream = tk.Button(
            view_bottom, text=t("btn_live"), font=self.f_btn, bg=BUTTON_BG, fg=TEXT_FG,
            command=self.on_toggle_live_stream,
        )
        self.btn_live_stream.pack(side="right", padx=4)
        Tooltip(self.btn_live_stream, lambda: t("tip_live"))

        self.btn_streamer_window = tk.Button(
            view_bottom, text=t("btn_popout"), font=self.f_btn, bg=BUTTON_BG, fg=TEXT_FG,
            command=self.on_open_streamer_window,
        )
        self.btn_streamer_window.pack(side="right", padx=4)
        bind(self.btn_streamer_window, "btn_popout")
        Tooltip(self.btn_streamer_window, lambda: t("tip_popout"))

        # Right pane: Diagnostics & Pattern tools
        ctrl_frame = tk.LabelFrame(mid_paned, font=self.f_bold, padx=8, pady=6, bg=WINDOW_BG, fg=TEXT_FG)
        bind(ctrl_frame, "ctrl_title")
        mid_paned.add(ctrl_frame, weight=2)

        # Target selection
        row_target = tk.Frame(ctrl_frame, bg=WINDOW_BG)
        row_target.pack(fill="x", pady=4)
        lbl_device = tk.Label(row_target, font=self.f_label, bg=WINDOW_BG, fg=TEXT_FG)
        bind(lbl_device, "lbl_device")
        lbl_device.pack(side="left")
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
        Tooltip(self.combo_device, lambda: t("tip_device_combo"))

        lbl_station = tk.Label(row_target, font=self.f_label, bg=WINDOW_BG, fg=TEXT_FG)
        bind(lbl_station, "lbl_station")
        lbl_station.pack(side="left", padx=(8, 0))
        self.var_sel_station = tk.StringVar(value="")
        tk.Label(
            row_target, textvariable=self.var_sel_station, font=self.f_mono, width=6, anchor="w",
            bg=WINDOW_BG, fg=TEXT_FG,
        ).pack(side="left", padx=4)

        # Manual Check button
        check_box = tk.LabelFrame(ctrl_frame, font=self.f_label, padx=6, pady=4, bg=WINDOW_BG, fg=TEXT_FG)
        bind(check_box, "check_title")
        check_box.pack(fill="x", pady=6)

        row_check_opt = tk.Frame(check_box, bg=WINDOW_BG)
        row_check_opt.pack(fill="x", pady=2)
        lbl_timeout = tk.Label(row_check_opt, font=self.f_label, bg=WINDOW_BG, fg=TEXT_FG)
        bind(lbl_timeout, "lbl_timeout")
        lbl_timeout.pack(side="left")
        self.var_timeout = tk.StringVar(value="5.0")
        timeout_entry = tk.Entry(
            row_check_opt, textvariable=self.var_timeout, width=5, font=self.f_entry, bg=ENTRY_BG, fg=TEXT_FG
        )
        timeout_entry.pack(side="left", padx=4)
        Tooltip(timeout_entry, lambda: t("tip_timeout"))
        Tooltip(lbl_timeout, lambda: t("tip_timeout"))

        self.btn_run_check = tk.Button(
            check_box, text=t("btn_check"), font=self.f_bold, fg=INFO_FG, bg=BUTTON_BG,
            command=self.on_run_manual_check,
        )
        self.btn_run_check.pack(fill="x", pady=4)
        Tooltip(self.btn_run_check, lambda: t("tip_check"))

        self.lbl_check_result = tk.Label(
            check_box, text=t("check_none"), font=self.f_bold, fg=MUTED_FG, bg=WINDOW_BG, anchor="w"
        )
        self.lbl_check_result.pack(anchor="w", pady=2)
        bind(self.lbl_check_result, "check_none")

        # Pattern creation
        pat_box = tk.LabelFrame(ctrl_frame, font=self.f_label, padx=6, pady=4, bg=WINDOW_BG, fg=TEXT_FG)
        bind(pat_box, "pat_title")
        pat_box.pack(fill="x", pady=6)

        self.btn_create_pattern = tk.Button(
            pat_box, text=t("btn_pattern"), font=self.f_btn, bg=BUTTON_BG, fg=TEXT_FG,
            command=self.on_create_pattern,
        )
        self.btn_create_pattern.pack(fill="x", pady=2)
        bind(self.btn_create_pattern, "btn_pattern")
        if PatternCropper is None:
            self.btn_create_pattern.config(state="disabled")
        Tooltip(self.btn_create_pattern,
                lambda: t("pattern_unavailable") if PatternCropper is None else t("tip_pattern"))

        self.btn_match_results = tk.Button(
            pat_box, text=t("btn_match"), font=self.f_btn, bg=BUTTON_BG, fg=TEXT_FG,
            command=self.on_show_match_results,
        )
        self.btn_match_results.pack(fill="x", pady=2)
        bind(self.btn_match_results, "btn_match")
        Tooltip(self.btn_match_results, lambda: t("tip_match"))

        # ----------------------------------------------------------------------
        # Bottom: Log Viewer
        # ----------------------------------------------------------------------
        log_frame = tk.LabelFrame(self, font=self.f_bold, padx=6, pady=4, bg=WINDOW_BG, fg=TEXT_FG)
        bind(log_frame, "log_title")
        log_frame.pack(fill="both", expand=True, padx=PADX, pady=(GAP, GAP))

        txt_wrap = tk.Frame(log_frame, bg=WINDOW_BG)
        txt_wrap.pack(fill="both", expand=True)
        vscroll = tk.Scrollbar(txt_wrap, orient="vertical", bg=WINDOW_BG)
        hscroll = tk.Scrollbar(txt_wrap, orient="horizontal", bg=WINDOW_BG)
        self.log_text = tk.Text(
            txt_wrap,
            height=6,
            font=self.f_mono,
            bg=LOG_BG,
            fg=LOG_FG,
            insertbackground=LOG_FG,
            wrap="none",
            yscrollcommand=vscroll.set,
            xscrollcommand=hscroll.set,
        )
        self.log_text.tag_configure("error", foreground=LOG_ERROR_FG)
        self.log_text.tag_configure("warn", foreground=LOG_WARN_FG)
        vscroll.config(command=self.log_text.yview)
        hscroll.config(command=self.log_text.xview)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        txt_wrap.rowconfigure(0, weight=1)
        txt_wrap.columnconfigure(0, weight=1)

        self._log_key("log_started")
        self._log_key("log_target", addr=f"{self.client.host}:{self.client.port}")
        if self._tk_scaling is not None:
            self._append_log(
                f"Windows DPI: {_DPI_AWARENESS_MODE}, tk scaling {self._tk_scaling:.3f}",
                f"Windows DPI 設定：{_DPI_AWARENESS_MODE}，tk scaling {self._tk_scaling:.3f}",
            )
        if PatternCropper is None:
            self._log_key("log_pattern_unavailable", level="error", detail=format_exception_text(_PATTERN_IMPORT_ERROR))

    # --------------------------------------------------------------------------
    # Station Cards Dashboard Rendering
    # --------------------------------------------------------------------------
    def _render_station_cards(self) -> None:
        """Render one status light per configured test station."""
        for w in self.stations_container.winfo_children():
            w.destroy()
        self._station_widgets.clear()

        # Each station gets its own block of rows (DFU, FCT, BT, then any other), three cards per row.
        counts: Dict[str, int] = {}
        for st in self._stations:
            name = str(st.get("station", "UNKNOWN"))
            counts[name] = counts.get(name, 0) + 1
        ordered = sorted(counts, key=lambda n: (STATION_ORDER.index(n) if n in STATION_ORDER else len(STATION_ORDER), n))
        block_start: Dict[str, int] = {}
        next_row = 0
        for name in ordered:
            block_start[name] = next_row
            next_row += (counts[name] + CARDS_PER_ROW - 1) // CARDS_PER_ROW
        seen: Dict[str, int] = {}
        for st in self._stations:
            st_name = str(st.get("station", "UNKNOWN"))
            dev_id = str(st.get("device", ""))
            status = str(st.get("status", "DISCONNECTED")).upper()
            index = seen.get(st_name, 0)
            seen[st_name] = index + 1
            row = block_start[st_name] + index // CARDS_PER_ROW
            col = index % CARDS_PER_ROW

            card = tk.Frame(self.stations_container, relief="ridge", bd=1, padx=8, pady=4, bg=WINDOW_BG)
            card.grid(row=row, column=col, sticky="nsew", padx=4, pady=2)

            # Header: the device ID is the title; the station name sits in the body.
            header = tk.Frame(card, bg=WINDOW_BG)
            header.pack(fill="x")
            lbl_title = tk.Label(header, text=dev_id, font=self.f_bold, bg=WINDOW_BG, fg=TEXT_FG)
            lbl_title.pack(side="left")

            state: Dict[str, Any] = {"station": st_name, "status": status}
            light = StatusLight(header, bg=WINDOW_BG)
            light.pack(side="right")
            light.set_status(status)
            Tooltip(light, lambda s=state: t(ui_i18n.station_light_tip_key(s["status"])))

            lbl_dev = tk.Label(card, text=t("station_label", station=st_name), font=self.f_mono, fg=MUTED_FG, bg=WINDOW_BG)
            lbl_dev.pack(anchor="w", pady=(2, 4))
            lbl_metrics = tk.Label(
                card, text=format_reading_metrics(st), font=self.f_mono, fg=MUTED_FG, bg=WINDOW_BG, justify="left"
            )
            lbl_metrics.pack(anchor="w", pady=(0, 4))

            # Select button: an icon only, so its tip says what it does
            btn_sel = tk.Button(
                card,
                text="👁",
                font=self.f_icon,
                bg=BUTTON_BG,
                fg=TEXT_FG,
                command=lambda d=dev_id: self._select_device(d),
            )
            btn_sel.pack(fill="x")
            Tooltip(btn_sel, lambda: t("tip_select"))

            self._station_widgets[dev_id] = {
                "card": card,
                "light": light,
                "device": lbl_dev,
                "metrics": lbl_metrics,
                "state": state,
            }

    def _station_of(self, device: str) -> str:
        for st in self._stations:
            if str(st.get("device", "")) == device:
                return str(st.get("station", ""))
        return ""

    def _select_device(self, device: str) -> None:
        self.var_sel_device.set(device)
        self.var_sel_station.set(self._station_of(device))
        self._log_key("log_selected_device", device=device, station=self.var_sel_station.get())
        # Trigger immediate snapshot for newly selected device
        self.on_capture_snapshot()

    def _on_device_selected(self, _event: Any = None) -> None:
        self.var_sel_station.set(self._station_of(self.var_sel_device.get()))

    def _on_language_changed(self) -> None:
        """Re-label the parts whose text is built from data, and the buttons whose label changes."""
        for dev_id, w in self._station_widgets.items():
            w["device"].config(text=t("station_label", station=w["state"]["station"]))
            st = next((s for s in self._stations if str(s.get("device", "")) == dev_id), None)
            if st is not None:
                w["metrics"].config(text=format_reading_metrics(st))
        self.btn_live_stream.config(text=t("btn_live_stop") if self._streaming_active else t("btn_live"))
        if str(self.btn_run_check.cget("state")) != "disabled":
            self.btn_run_check.config(text=t("btn_check"))

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

    def _core_tooltip_text(self) -> str:
        """Hover tip for the Core Service lamp: uptime, connection count and busy devices."""
        if self._service_ok:
            info = self._service_info
            busy_list = info.get("busy_devices", []) or []
            busy = t("sep_list").join(str(d) for d in busy_list) or t("core_none")
            return (
                t("core_ok_tip", seconds=info.get("uptime_seconds", 0), clients=info.get("active_connections", 0))
                + "\n"
                + t("core_busy", busy=busy)
            )
        return t("core_down")

    def _update_status_ui(self, status_res: Dict[str, Any]) -> None:
        if status_res.get("status") == "ok":
            self._service_ok = True
            self._service_info = status_res
            self.light_core.set_light("green")

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
                        w["light"].set_status(st.get("status", "DISCONNECTED"))
                        w["state"]["status"] = str(st.get("status", "DISCONNECTED")).upper()
                        w["metrics"].config(text=format_reading_metrics(st))
        else:
            # Log once per outage (first poll or connected -> disconnected), not on every 1.5 s poll.
            was_ok = self._service_ok
            self._service_ok = False
            self._service_info = status_res
            self.light_core.set_light("red")
            if was_ok is not False:
                msg = status_res.get("message") or status_res.get("error")
                error = (str(msg), str(msg)) if msg else pair("err_conn_lost")
                self._log_key("log_core_down", level="error", error=error)
            for w in self._station_widgets.values():
                w["light"].set_status("DISCONNECTED")
                w["state"]["status"] = "DISCONNECTED"

    # --------------------------------------------------------------------------
    # Logging & Threading Queue Plumbing
    # --------------------------------------------------------------------------
    def _append_log(self, en: str, zh: str = "", level: str = "info") -> Optional[str]:
        """Write one bilingual line to today's log file and the current-language half to the panel.

        Warning and error lines return a mark the HMI can jump to. ``level`` is ``"info"``,
        ``"warn"`` (orange) or ``"error"`` (red).
        """
        zh = zh or en
        now = datetime.datetime.now()
        try:
            self._file_log.write(en, zh, when=now)
        except OSError:
            # A full or read-only disk must not stop the UI; the Log panel still shows the line.
            pass
        text = zh if ui_i18n.current_language() == "zh" else en
        start = self.log_text.index("end-1c")
        self.log_text.insert("end", f"{now.strftime('%H:%M:%S')} | {text}\n")
        self.log_text.see("end")
        if level not in ("error", "warn"):
            return None
        self.log_text.tag_add(level, start, "end-1c")
        self._log_seq += 1
        mark = f"log_{self._log_seq}"
        self.log_text.mark_set(mark, start)
        self.log_text.mark_gravity(mark, "left")
        return mark

    def _log_key(self, key: str, level: str = "info", **kwargs: Any) -> Optional[str]:
        return self._append_log(*_bilingual(key, **kwargs), level=level)

    def _jump_to_log(self, mark: str) -> None:
        self.log_text.see(mark)

    def _report_error(self, label: tk.Label, brief_key: str, detail: Tuple[str, str], level: str = "error") -> Optional[str]:
        """Show a short message on the HMI, write the full detail to the Log, and make the label jump there."""
        mark = self._append_log(*detail, level=level)
        fg = ERROR_FG if level == "error" else WARN_FG
        bind(label, lambda: f"{t(brief_key)}{t('see_log')}")
        label.config(fg=fg, cursor="hand2")
        label.bind("<Button-1>", lambda _event, m=mark: self._jump_to_log(m))
        return mark

    @staticmethod
    def _set_info(label: tk.Label, text: Any, fg: str) -> None:
        """Plain (non-error) label text. A callable is re-run on a language switch; clears any click-to-log binding."""
        render = text if callable(text) else (lambda: text)
        bind(label, render)
        label.config(fg=fg, cursor="")
        label.unbind("<Button-1>")

    @staticmethod
    def _core_error_pair(res: Dict[str, Any]) -> Tuple[str, str]:
        """The core's own error text (same in both languages), or the generic unknown-error text."""
        msg = res.get("message") or res.get("error")
        return (str(msg), str(msg)) if msg else pair("err_unknown")

    def _drain_queues(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._append_log(msg, msg)
        except queue.Empty:
            pass
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    self._log_key("log_ui_callback_error", level="error", error=str(exc))
        except queue.Empty:
            pass
        if not self._is_closing:
            self.after(100, self._drain_queues)

    # --------------------------------------------------------------------------
    # Language switch
    # --------------------------------------------------------------------------
    def on_toggle_language(self) -> None:
        """🌐: switch every open window to the other language and remember the choice."""
        new = "zh" if ui_i18n.current_language() == "en" else "en"
        ui_i18n.set_language(new)
        if not ui_i18n.save_language(new):
            self._log_key("log_prefs_fail", level="warn")

    # --------------------------------------------------------------------------
    # Snapshot & Live Stream Handling
    # --------------------------------------------------------------------------
    def on_open_device_settings(self) -> None:
        """Open the device list settings page (works without a running Core Service)."""
        if DeviceSettingsWindow is None:
            self._log_key("log_settings_missing", level="error")
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
        """Stop and start Core Service on this machine; the settings page checks for busy devices first.

        Runs on a worker thread: the full error goes to the Log through the UI queue.
        """
        from core_daemon import restart_core_daemon

        try:
            res = restart_core_daemon(host=self.client.host, port=self.client.port, timeout=10.0)
        except Exception as exc:
            res = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        if res.get("status") == "ok":
            return {"status": "ok", "message": res.get("message", res)}
        detail = str(res.get("message", res))
        self._ui_queue.put(lambda: self._log_key("log_restart_fail", level="error", detail=detail))
        return {"status": "error", "message": t("err_restart") + t("see_log")}

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
        self._log_key("log_connect_target", addr=f"{host}:{port}")
        self.client.disconnect()
        threading.Thread(
            target=lambda: self.client.connect(timeout=2.0), daemon=True
        ).start()

    def on_capture_snapshot(self) -> None:
        """Request a single snapshot frame from Core Service."""
        device = self.var_sel_device.get().strip()
        if not device:
            self._log_key("log_no_device_snapshot", level="warn")
            return
        self._log_key("log_snapshot_request", device=device)

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
                    stamp = time.strftime("%H:%M:%S")
                    self._set_info(self.lbl_preview_info, lambda: t("preview_info", w=w, h=h, time=stamp), MUTED_FG)
                    self._log_key("log_snapshot_ok", w=w, h=h)
                except Exception as exc:
                    self._report_error(self.lbl_preview_info, "err_decode", pair("log_snapshot_decode", error=str(exc)))
            else:
                self._log_key("log_snapshot_empty", level="warn")
        else:
            self._report_error(self.lbl_preview_info, "err_snapshot", _bilingual("log_snapshot_fail", error=self._core_error_pair(res)))

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
            self.btn_live_stream.config(text=t("btn_live"))
            self._log_key("log_stream_off")
        else:
            self._streaming_active = True
            self.btn_live_stream.config(text=t("btn_live_stop"))
            self._log_key("log_stream_on")
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

                        def update_ui(img=pil_img, info=(w, h, time.strftime("%H:%M:%S"))):
                            self._render_image_on_canvas(img)
                            self._set_info(self.lbl_preview_info,
                                           lambda: t("preview_info", w=info[0], h=info[1], time=info[2]), MUTED_FG)

                        self._ui_queue.put(update_ui)
                    except Exception:
                        pass
            time.sleep(0.3)

    def on_open_streamer_window(self) -> None:
        """Open a standalone StreamerWindow using current snapshot stream getter."""
        if StreamerWindow is None:
            self._log_key("log_streamer_missing", level="error")
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
            self._log_key("log_popout_close")

        self._streamer_window = StreamerWindow(
            self,
            frame_getter=frame_getter,
            input_sender=None,  # Readonly monitoring
            fps=15,
            title=t("stream_title", device=self.var_sel_device.get()),
            on_close=on_close,
        )
        bind(self._streamer_window, lambda device=self.var_sel_device.get(): t("stream_title", device=device),
             option="title")
        self._log_key("log_popout_open")

    # --------------------------------------------------------------------------
    # Manual Diagnostics & Pattern Creation
    # --------------------------------------------------------------------------
    def on_run_manual_check(self) -> None:
        """Trigger a manual check command over TCP."""
        device = self.var_sel_device.get().strip()
        if not device:
            self._log_key("log_no_device_check", level="warn")
            return
        try:
            timeout_sec = float(self.var_timeout.get().strip())
        except ValueError:
            timeout_sec = 5.0

        self._log_key("log_inspect", device=device, timeout=timeout_sec)
        self.btn_run_check.config(state="disabled", text=t("btn_checking"))
        self._set_info(self.lbl_check_result, lambda: t("check_running"), INFO_FG)

        def worker() -> None:
            res = self.client.manual_check(
                device=device,
                timeout_sec=timeout_sec,
            )
            self._ui_queue.put(lambda: self._display_check_result(res))

        threading.Thread(target=worker, daemon=True).start()

    def _display_check_result(self, res: Dict[str, Any]) -> None:
        self.btn_run_check.config(state="normal", text=t("btn_check"))
        status = res.get("status", "error")
        if status == "ok":
            self._set_info(
                self.lbl_check_result,
                lambda: format_manual_check(res),
                OK_FG if res.get("active") else MUTED_FG,
            )
            self._append_log(format_manual_check(res, "en"), format_manual_check(res, "zh"))
            return
        level = "warn" if status == "timeout" or res.get("error") == "device_busy" else "error"
        detail = (format_manual_check(res, "en"), format_manual_check(res, "zh"))
        self._report_error(self.lbl_check_result, manual_check_error_key(res), detail, level=level)

    def on_save_capture(self) -> bool:
        """Save latest snapshot frame to template capture path. Returns False when nothing was saved."""
        if self._latest_pil_image is None:
            self._log_key("log_no_capture", level="warn")
            return False
        save_path = self._templates.capture_path()
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            self._latest_pil_image.save(save_path)
        except Exception as exc:
            self._log_key("log_save_fail", level="error", error=str(exc))
            return False
        self._log_key("log_saved_capture", path=str(save_path))
        return True

    def on_create_pattern(self) -> None:
        """Save the latest snapshot as the capture, then open the pattern cropper on it."""
        if PatternCropper is None:
            self._log_key("log_pattern_unavailable", level="error", detail=format_exception_text(_PATTERN_IMPORT_ERROR))
            return
        # The latest snapshot replaces the old capture; with no snapshot yet, reuse the saved one.
        if self._latest_pil_image is not None and not self.on_save_capture():
            return
        src = self._templates.capture_path()
        if not src.exists():
            self._log_key("log_need_snapshot", level="warn")
            return
        PatternCropper(
            self,
            self._templates,
            str(src),
            device=self.var_sel_station.get().strip().upper(),
            template_key="window",
            on_saved=self._on_template_saved,
        )

    def _on_template_saved(self, path: str) -> None:
        self._log_key("log_template_saved", path=path)

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

        ui_i18n.off_language_change(self._on_language_changed)
        self._file_log.close()
        self.destroy()


# ==============================================================================
# Windows DPI (issue #107)
# ==============================================================================

def enable_windows_dpi_awareness(windll: Any = None) -> str:
    """Declare this process DPI-aware so Windows does not bitmap-stretch the Tk window at 125% / 150%.

    Must run before the first Tk root is created. Returns which call took effect:
    ``"shcore"``, ``"user32"`` (fallback), ``"failed"``, or ``"skipped"`` on non-Windows (nothing is touched).
    ``windll`` is injectable for tests; the default is ``ctypes.windll``.
    """
    if not sys.platform.startswith("win"):
        return "skipped"
    if windll is None:
        import ctypes
        windll = ctypes.windll
    try:
        if windll.shcore.SetProcessDpiAwareness(1) == 0:  # PROCESS_SYSTEM_DPI_AWARE; S_OK
            return "shcore"
    except Exception:  # shcore missing (pre-8.1) or the call itself raised
        pass
    try:
        if windll.user32.SetProcessDPIAware():  # old system-wide awareness; BOOL, nonzero = ok
            return "user32"
    except Exception:
        pass
    return "failed"


def apply_windows_tk_scaling(root: Any) -> Optional[float]:
    """Set Tk scaling from the real screen DPI (Tk's unit is pixels per point = DPI / 72). No-op off Windows.

    Returns the scaling that was applied, or ``None`` when nothing was touched.
    """
    if not sys.platform.startswith("win"):
        return None
    dpi = float(root.winfo_fpixels("1i"))  # pixels in one inch on the window's current screen
    scaling = dpi / 72.0
    root.tk.call("tk", "scaling", scaling)
    return scaling


# Filled in by main(); AtlasUI writes it to the Log so a field report shows which DPI path ran.
_DPI_AWARENESS_MODE = "not-run"


def runtime_self_test() -> List[str]:
    """Import every runtime dependency the Monitor UI needs. Returns the problems found (empty = OK).

    Run from the source tree in CI, and against the frozen exe (``AtlasKVM.exe --self-test``), so a
    package that PyInstaller failed to bundle fails the build instead of the operator's first launch.
    """
    import importlib

    problems: List[str] = []
    for name in ("cv2", "av", "aiortc", "onnxruntime", "rapidocr_onnxruntime", "PIL", "tkinter"):
        try:
            importlib.import_module(name)
        except Exception as exc:
            problems.append(f"import {name}: {exc!r}")
    checks = {
        "_TK_OK": _TK_OK,
        "_PIL_OK": _PIL_OK,
        "_CV2_OK": _CV2_OK,
        "PatternCropper": PatternCropper is not None,
        "StreamerWindow": StreamerWindow is not None,
        "DeviceSettingsWindow": DeviceSettingsWindow is not None,
    }
    problems += [f"{flag} is not set ({_PATTERN_IMPORT_ERROR!r})" for flag, ok in checks.items() if not ok]
    return problems


# ==============================================================================
# Main Entry Point
# ==============================================================================

def main() -> None:
    global _DPI_AWARENESS_MODE
    _DPI_AWARENESS_MODE = enable_windows_dpi_awareness()  # must run before the first Tk root is created
    parser = argparse.ArgumentParser(description="Atlas2 Lightweight Monitor Client")
    parser.add_argument("--host", default="127.0.0.1", help="Core Service TCP host")
    parser.add_argument("--port", type=int, default=5000, help="Core Service TCP port")
    parser.add_argument(
        "--headless-check",
        action="store_true",
        help="Perform a quick TCP ping/status check and exit without GUI",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Import every runtime dependency, print the result and exit (non-zero on failure)",
    )
    args = parser.parse_args()

    if args.self_test:
        problems = runtime_self_test()
        if sys.stdout is not None:  # the --windowed exe has no console; the exit code is the signal there
            print("self-test OK" if not problems else "self-test FAILED:\n  " + "\n  ".join(problems))
        sys.exit(1 if problems else 0)

    if args.headless_check:
        client = CoreServiceClient(host=args.host, port=args.port)
        status = client.get_status(timeout=3.0)
        print(json.dumps(status, indent=2))
        sys.exit(0 if status.get("status") == "ok" else 1)

    app = AtlasUI(core_host=args.host, core_port=args.port)
    app.mainloop()


if __name__ == "__main__":
    main()
