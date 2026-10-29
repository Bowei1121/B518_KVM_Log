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
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import tkinter as tk
    from tkinter import font as tkfont, messagebox, ttk
    _TK_OK = True
except Exception:
    tk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    tkfont = None  # type: ignore[assignment]
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

import alarm_banner
from app_info import about_text, asset_path, is_ci_build, version
from core_channel import CoreChannel, channel_name_for
from core_protocol import flatten_reply
from match_diagnostics import MatchDiagnostics, note_for_language
from station_list_hint import list_hint_text
from station_presentation import present_station
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
    OFFLINE_FG,
    OK_FG,
    PANEL_BG,
    PREVIEW_BG,
    TEXT_FG,
    WARN_FG,
    WINDOW_BG,
    StatusLight,
    Tooltip,
    apply_light_ttk_style,
    apply_window_icon,
    fit_label_to_pixels,
    fix_text_widths,
    measure_all_languages,
    bind_clamped,
    ROW_BLINK_DIM,
    draw_alarm_heart,
    draw_alarm_square_x,
    draw_alarm_triangle,
    full_text,
    reserve_text_width,
    pin_light_appearance,
)
import wheel_scroll
from window_placement import frame_size, open_geometry, placed_area, usable_client_size

# Station list (spec v5.3 §14.10): 12 × 56 px + 11 × 4 px = 716 px, no gap above the first row.
# Up to four station groups add 4 px per prefix change (at most 728 px); more groups use only 4 px gaps.
# A 16 px track sits beside the rows.
ROW_W, ROW_H, ROW_PITCH = 176, 56, 60
LIST_VIEW_H = 728
STATION_PANEL_MIN_H = 780
LIST_HINT_H, LIST_HINT_MAX_W = 16, 188
TRACK_W, THUMB_W, THUMB_MIN_H = 16, 13, 40
TRACK_BG, TRACK_EDGE, THUMB_BG, THUMB_ACTIVE = "#ECEFF1", "#CFD8DC", "#90A4AE", "#607D8B"
ALARM_RED, HINT_GREY = "#B71C1C", "#546E7A"
# Alarm banner (spec v5.3 §13.3): 120 px high, 72 px icon, 24 px side padding, 22 px icon-to-text gap.
BANNER_H, BANNER_ICON, BANNER_PAD, BANNER_GAP = 120, 72, 24, 22

# Smallest live view, as a share of its DPI size, when the usable height is short (user decision, issue #117 comment
# 6342904432): 70%, so 200% gives 672×378. Shared with tests/test_monitor_hmi_115.py; do not loosen it elsewhere.
PREVIEW_MIN_SHARE = 0.7

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


def connect_failure_tip(station: Dict[str, Any], pres: Any, seconds_left: Optional[int],
                        lang: Optional[str] = None) -> str:
    """Row tooltip lines of a Retry / Connection failure row, each ending in a newline (issue #134); else ''."""
    if pres.alarm_kind != "connect_failed" and pres.light != "orange":
        return ""
    count = int(station.get("connect_fail_count") or 0)
    total = int(pres.status_args.get("total", station.get("retry_threshold") or 3))
    lines = [t("row_tip_conn_count", lang, n=min(count, total), total=total)]
    if count > total:  # the consecutive line is capped at the threshold; show the cumulative count apart
        lines.append(t("row_tip_conn_total", lang, n=count))
    if station.get("first_fail_at") is not None:
        lines.append(t("row_tip_first_fail", lang,
                       time=time.strftime("%H:%M:%S", time.localtime(float(station["first_fail_at"])))))
    if station.get("last_connect_error"):
        lines.append(t("row_tip_last_error", lang, code=station["last_connect_error"]))
    if seconds_left is not None:
        lines.append(t("row_tip_next_retry", lang, s=seconds_left))
    if pres.alarm_kind == "connect_failed":  # plain text: a tooltip cannot be red (its red styling is deferred to #150)
        lines.append(t("row_tip_next_step", lang))
    return "\n".join(lines) + "\n"


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
        wheel_scroll.install(self)
        self.catalog = catalog
        bind(self, "match_title", option="title")
        self.geometry("980x700")
        self.configure(bg=WINDOW_BG)
        pin_light_appearance(self)
        apply_window_icon(self)
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
        wheel_scroll.register(self.listbox)
        Tooltip(self.listbox, lambda: t("match_tip_list"))
        self.image_label = tk.Label(
            right, text=t("match_pick"), anchor="center", bg=WINDOW_BG, fg=TEXT_FG
        )
        self.image_label.pack(fill="both", expand=True)
        self.load()
        fix_text_widths(self, ui_i18n.LANGUAGES, ui_i18n.current_language, ui_i18n.set_language)  # #117
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
        wheel_scroll.install(self)  # one app-wide wheel handler (#117)
        ui_i18n.set_language(ui_i18n.load_saved_language())
        bind(self, "win_title", option="title", version=version())
        apply_window_icon(self)
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
        self._clock = time.monotonic  # the Retry countdown reads it (tests replace it)
        self._row_blink_job: Optional[str] = None  # ONE shared 0.5 s job for every Connection failure row (#134)
        self._row_blink_lit = True
        self._retry_countdown_job: Optional[str] = None  # ONE 1 s job while a Retry row is waiting (#134)

        # Core Service state: None until the first poll answers; True/False after that.
        self._service_ok: Optional[bool] = None
        self._service_info: Dict[str, Any] = {}

        # Polling
        self._is_closing = False

        self._build_widgets()
        ui_i18n.on_language_change(self._on_language_changed)

        self._fit_window()

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
        # Top row, Facebook style (issue #115): FOXLINK logo on the left; 🌐 and ⚙ on the right.
        # The gear is packed first so it sits at the far right and 🌐 sits just left of it.
        header = tk.Frame(self, bg=WINDOW_BG)
        header.pack(fill="x", padx=PADX, pady=(GAP, 0))
        self.btn_device_settings = tk.Button(
            header, text="⚙", font=self.f_gear, bg=BUTTON_BG, fg=TEXT_FG,
            command=self.on_open_device_settings,
        )
        self.btn_device_settings.pack(side="right", padx=(4, 0))
        Tooltip(self.btn_device_settings, lambda: t("tip_settings"))
        self.btn_lang = tk.Button(
            header, text="🌐", font=self.f_icon, bg=BUTTON_BG, fg=TEXT_FG, command=self.on_toggle_language,
        )
        self.btn_lang.pack(side="right", padx=4)
        Tooltip(self.btn_lang, lambda: t("tip_lang"))

        logo = self._load_logo()
        self._logo_image = logo  # Tk drops an image that nothing references
        if logo is not None:
            self.lbl_logo = tk.Label(header, image=logo, bg=WINDOW_BG, fg=TEXT_FG, bd=0)
        else:
            self.lbl_logo = tk.Label(header, text="FOXLINK", font=self.f_large_bold, fg=INFO_FG, bg=WINDOW_BG)
        self.lbl_logo.pack(side="left")
        self._logo_tip = Tooltip(self.lbl_logo, about_text)

        # ----------------------------------------------------------------------
        # Top Bar: Core Service Connection
        # ----------------------------------------------------------------------
        top_frame = self.top_frame = tk.LabelFrame(self, font=self.f_bold, padx=8, pady=6, bg=WINDOW_BG, fg=TEXT_FG)
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
        # Body: fixed 200 px station panel on the left (issue #126), live view, controls and Log on the right
        # ----------------------------------------------------------------------
        self._build_alarm_banner()  # hidden until a device is abnormal; shown between the top bar and the body
        body = self.body = tk.Frame(self, bg=WINDOW_BG)
        body.pack(fill="both", expand=True, padx=PADX, pady=GAP)
        # Height is computed below from the title insets, list and hint line, with a 780 px floor.
        self.station_panel = tk.LabelFrame(
            body, font=self.f_bold, width=200, height=STATION_PANEL_MIN_H, padx=2, pady=4,
            bg=WINDOW_BG, fg=TEXT_FG,
        )
        bind(self.station_panel, "dash_title")
        self.station_panel.pack(side="left", fill="y")
        self.station_panel.pack_propagate(False)
        right = tk.Frame(body, bg=WINDOW_BG)
        right.pack(side="left", fill="both", expand=True, padx=(GAP, 0))
        log_frame = tk.LabelFrame(right, font=self.f_bold, padx=6, pady=4, bg=WINDOW_BG, fg=TEXT_FG)
        bind(log_frame, "log_title")
        # Packed before the middle pane: the Log always keeps its lines (issue #115).
        log_frame.pack(side="bottom", fill="x", pady=(GAP, 0))

        txt_wrap = tk.Frame(log_frame, bg=WINDOW_BG)
        txt_wrap.pack(fill="both", expand=True)
        vscroll = tk.Scrollbar(txt_wrap, orient="vertical", bg=WINDOW_BG)
        self.log_text = tk.Text(
            txt_wrap,
            height=6,
            font=self.f_mono,
            bg=LOG_BG,
            fg=LOG_FG,
            insertbackground=LOG_FG,
            wrap="word",  # long lines wrap; the Log file keeps every line whole (issue #115)
            yscrollcommand=vscroll.set,
        )
        self.log_text.tag_configure("error", foreground=LOG_ERROR_FG)
        self.log_text.tag_configure("warn", foreground=LOG_WARN_FG)
        vscroll.config(command=self.log_text.yview)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        wheel_scroll.register(self.log_text)
        txt_wrap.rowconfigure(0, weight=1)
        txt_wrap.columnconfigure(0, weight=1)

        # ----------------------------------------------------------------------
        # Station Real-Time Status Dashboard
        # ----------------------------------------------------------------------
        # Spec v5.3 §14.10: the list always shows exactly 12 rows (728 px) beside a fixed 16 px scroll track,
        # and a 16 px hint line right under the list names rows that are off-screen.
        hint_frame = tk.Frame(self.station_panel, height=LIST_HINT_H, bg=WINDOW_BG)
        hint_frame.pack_propagate(False)
        # root=self: without it the font lands in whichever Tk root is the default; this window then does not know
        # the name and falls back to a 12 pt font that grows with scaling (cut at 200% on Windows CI).
        self._list_hint_font = tkfont.Font(root=self, family=self.f_label[0], size=-12, weight="bold")
        self.lbl_list_hint = tk.Label(hint_frame, text="", font=self._list_hint_font, anchor="w",
                                      bg=WINDOW_BG, bd=0, padx=0, pady=0)
        self.lbl_list_hint.pack(fill="both", expand=True)
        # A fixed one-line width, so the #117 width lock leaves it alone (it starts empty and would get 1 char).
        fit_label_to_pixels(self.lbl_list_hint, LIST_HINT_MAX_W + 4, wrap=False)
        # 16 px, unless the font's line is taller (e.g. a CJK font): never cut the hint (every char visible).
        hint_frame.configure(height=max(LIST_HINT_H, self.lbl_list_hint.winfo_reqheight()))
        title_linespace = int(self.tk.call("font", "metrics", self.station_panel.cget("font"), "-linespace"))
        panel_bd = self.station_panel.winfo_pixels(self.station_panel.cget("bd"))
        panel_pady = self.station_panel.winfo_pixels(self.station_panel.cget("pady"))
        needed = (max(panel_bd, title_linespace + 2) + panel_pady + LIST_VIEW_H
                  + int(hint_frame.cget("height")) + panel_pady + panel_bd)
        self.station_panel.configure(height=max(STATION_PANEL_MIN_H, needed))
        self.station_list_frame = tk.Frame(self.station_panel, bg=WINDOW_BG)
        self.station_list_frame.pack(fill="x")
        hint_frame.pack(fill="x")  # directly under the 728 px list, as in spec shot v5.3-01
        self._list_content_h = LIST_VIEW_H
        self._list_row_tops: Dict[str, int] = {}
        self._list_above: List[str] = []
        self._list_below: List[str] = []
        self._track_drag: Optional[float] = None  # pointer offset inside the thumb while dragging
        self._track_hover = False
        self.station_canvas = tk.Canvas(
            self.station_list_frame, width=ROW_W, height=LIST_VIEW_H, highlightthickness=0, bd=0,
            bg=WINDOW_BG, yscrollincrement=ROW_PITCH, yscrollcommand=self._on_station_list_scrolled,
        )
        self.station_canvas.pack(side="left", anchor="n")
        wheel_scroll.register(self.station_canvas)  # one wheel notch = one 60 px row
        self.stations_container = tk.Frame(self.station_canvas, bg=WINDOW_BG)
        self.station_canvas.create_window(0, 0, anchor="nw", window=self.stations_container)
        self.station_track = tk.Canvas(self.station_list_frame, width=TRACK_W, height=LIST_VIEW_H,
                                       highlightthickness=0, bd=0, bg=TRACK_BG)
        self.station_track.pack(side="left", anchor="n")
        wheel_scroll.register(self.station_track, scrolls=self.station_canvas)
        wheel_scroll.register(hint_frame, scrolls=self.station_canvas)
        wheel_scroll.register(self.lbl_list_hint, scrolls=self.station_canvas)
        self.station_track.create_line(0, 0, 0, LIST_VIEW_H, fill=TRACK_EDGE, tags="edge")
        self.station_track.bind("<Button-1>", self._station_track_press)
        self.station_track.bind("<B1-Motion>", self._station_track_drag)
        self.station_track.bind("<ButtonRelease-1>", self._station_track_release)
        self.station_track.bind("<Motion>", self._station_track_motion)
        self.station_track.bind("<Leave>", lambda _e: self._station_track_motion(None))

        self._render_station_cards()

        # ----------------------------------------------------------------------
        # Middle: Split into Live Stream View & Controls
        # ----------------------------------------------------------------------
        # A grid, not a ttk.Panedwindow: the control column keeps its requested width; the live view takes the rest.
        mid = tk.Frame(right, bg=WINDOW_BG)
        mid.pack(fill="both", expand=True)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)

        # Left pane: Live Monitor Display
        view_frame = tk.LabelFrame(mid, font=self.f_bold, padx=6, pady=6, bg=WINDOW_BG, fg=TEXT_FG)
        bind(view_frame, "view_title")
        view_frame.grid(row=0, column=0, sticky="nsew")

        preview_w, preview_h = self._preview_size()
        self.canvas_preview = tk.Canvas(
            view_frame, bg=PREVIEW_BG, width=preview_w, height=preview_h, highlightthickness=0
        )
        self.canvas_preview.pack(fill="both", expand=True, padx=4, pady=4)

        # Two rows (issue #115 review): the status line on its own, the buttons below it, so neither row needs
        # the full width of the live view at once.
        view_status = tk.Frame(view_frame, bg=WINDOW_BG)
        view_status.pack(fill="x", pady=(4, 0))
        self.lbl_preview_info = tk.Label(
            view_status, text=t("preview_none"), font=self.f_mono, fg=MUTED_FG, bg=WINDOW_BG, anchor="w"
        )
        self.lbl_preview_info.pack(side="left", fill="x", expand=True)
        reserve_text_width(self.lbl_preview_info, 34, lines=1)  # run-time snapshot line: one fixed line, cut with … (R3-1)
        Tooltip(self.lbl_preview_info, lambda: full_text(self.lbl_preview_info))
        view_bottom = tk.Frame(view_frame, bg=WINDOW_BG)
        view_bottom.pack(fill="x", pady=(2, 0))
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
        ctrl_frame = tk.LabelFrame(mid, font=self.f_bold, padx=8, pady=6, bg=WINDOW_BG, fg=TEXT_FG)
        bind(ctrl_frame, "ctrl_title")
        ctrl_frame.grid(row=0, column=1, sticky="ns", padx=(GAP, 0))

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
        reserve_text_width(self.lbl_check_result, 34, lines=3)  # run-time result: three fixed lines, cut with … (R3-1)
        Tooltip(self.lbl_check_result, lambda: full_text(self.lbl_check_result))
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

        about = about_text()  # first Log line: version and build (issue #115)
        self._append_log(about, about)
        self._log_key("log_started")
        self._log_key("log_target", addr=f"{self.client.host}:{self.client.port}")
        if self._tk_scaling is not None:
            self._append_log(
                f"Windows DPI: {_DPI_AWARENESS_MODE}, tk scaling {self._tk_scaling:.3f}",
                f"Windows DPI 設定：{_DPI_AWARENESS_MODE}，tk scaling {self._tk_scaling:.3f}",
            )
        if PatternCropper is None:
            self._log_key("log_pattern_unavailable", level="error", detail=format_exception_text(_PATTERN_IMPORT_ERROR))

    def _load_logo(self) -> Any:
        """The FOXLINK logo, enlarged to the screen's DPI (native is 137×35 at 96 DPI). None when it cannot load.

        A logo that will not load must not stop the window, so any failure falls back to the text logo.
        """
        path = asset_path("foxlink_logo.png")
        try:
            if _PIL_OK:
                img = Image.open(path).convert("RGBA")
                scale = max(1.0, float(self.winfo_fpixels("1i")) / 96.0)
                if scale != 1.0:
                    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img, master=self)
            return tk.PhotoImage(master=self, file=str(path))
        except Exception:  # missing file, unreadable PNG, or Tk refusing it: the text logo takes over
            return None

    def _preview_size(self, shrink: float = 1.0) -> Tuple[int, int]:
        """Live-view size in pixels: 480×270 at 100% DPI, growing with the DPI so it never becomes a small box (#115).

        ``shrink`` is the share of that size kept when the usable screen height is too small (#117).
        """
        factor = (self._tk_scaling or (4 / 3)) / (4 / 3) * shrink
        return round(480 * factor), round(270 * factor)

    def _fit_window(self) -> None:
        """Open flush with the top and right of the usable screen, never taller than it (issue #117).

        Both languages are measured, and the label and button widths are already fixed to the longer of the two,
        so a language switch never changes the window size. When the content is taller than the usable height, the
        live preview shrinks in steps (down to half) first. The Log keeps its lines either way.
        """
        fix_text_widths(self, ui_i18n.LANGUAGES, ui_i18n.current_language, ui_i18n.set_language)
        area = placed_area(self)
        frame = frame_size()
        max_w, max_h = usable_client_size(area, frame)
        width, height = self._fit_preview_to(max_h)
        self.minsize(min(width, max_w), min(height, max_h))
        geometry = open_geometry(area, frame, (self.winfo_screenwidth(), self.winfo_screenheight()), (width, height))
        self.geometry(geometry)
        self._append_log(
            f"Window: work area {area.width}x{area.height} at {area.x},{area.y}; "
            f"frame L{frame.left} R{frame.right} T{frame.top} B{frame.bottom}; opened {geometry}",
            f"視窗：工作區 {area.width}x{area.height}（位置 {area.x},{area.y}），"
            f"外框 左{frame.left} 右{frame.right} 上{frame.top} 下{frame.bottom}，開啟 {geometry}",
        )

    def _fit_preview_to(self, max_h: int) -> Tuple[int, int]:
        """Measure the content at the full live-view size, then at smaller steps, until it fits ``max_h`` (#117)."""
        width = height = 0
        for shrink in (1.0, 0.85, PREVIEW_MIN_SHARE):
            preview_w, preview_h = self._preview_size(shrink)
            self.canvas_preview.configure(width=preview_w, height=preview_h)
            width, height = measure_all_languages(self, ui_i18n.LANGUAGES, ui_i18n.current_language,
                                                  ui_i18n.set_language)
            if height <= max_h:
                break
        return width, height

    # --------------------------------------------------------------------------
    # Station Cards Dashboard Rendering
    # --------------------------------------------------------------------------
    def _render_station_cards(self) -> None:
        """Build all devices in Core order, with at least twelve fixed cells."""
        for widget in self.stations_container.winfo_children():
            widget.destroy()
        self._station_widgets.clear()
        self._render_empty_hint()
        prefixes = [str(st.get("device", "")).split("-")[0] for st in self._stations]
        group_count = bool(prefixes) + sum(a != b for a, b in zip(prefixes, prefixes[1:]))
        previous_prefix = None
        self._list_row_tops = {}
        for index in range(max(12, len(self._stations))):
            st = self._stations[index] if index < len(self._stations) else None
            prefix = str(st.get("device", "")).split("-")[0] if st else previous_prefix
            gap = 0 if index == 0 else 4
            if group_count <= 4 and previous_prefix is not None and prefix != previous_prefix:
                gap += 4
            card = tk.Frame(self.stations_container, width=ROW_W, height=ROW_H, bg=WINDOW_BG)
            card.grid(row=index, column=0, padx=0, pady=(gap, 0))
            card.pack_propagate(False)
            card.grid_propagate(False)
            previous_prefix = prefix
            if st is None:
                continue
            dev_id = str(st.get("device", ""))
            state = {"station": str(st.get("station", "")), "row": dict(st),
                     "status": str(st.get("status", "DISCONNECTED")).upper(), "core_connected": True,
                     "retry_base": (self._clock(), st.get("retry_in_sec"))}
            fam, mono = self.f_label[0], self.f_mono[0]
            bar = tk.Frame(card, bg=WINDOW_BG)
            bar.place(x=0, y=0, width=8, height=56)
            light = StatusLight(card, diameter=12, bg=WINDOW_BG)
            light.place(x=12, y=7, width=16, height=16)
            icon = tk.Canvas(card, width=16, height=16, bg=WINDOW_BG, highlightthickness=0)  # ▲ / ♥ (drawn per row state), not the light
            icon.place(x=12, y=7, width=16, height=16)
            title = tk.Label(card, text=dev_id, font=(mono, -22, "bold"),
                             bg=WINDOW_BG, fg=TEXT_FG, anchor="w", bd=0, padx=0, width=1)
            title.place(x=30, y=0, width=76, height=28)
            lamp = tk.Canvas(card, width=16, height=16, bg=WINDOW_BG, highlightthickness=0)
            lamp_dot = lamp.create_oval(2, 2, 14, 14, width=3)
            lamp.place(x=106, y=6, width=16, height=16)
            online = tk.Label(card, font=(fam, -10, "bold"), bg=WINDOW_BG,
                              fg=TEXT_FG, anchor="w", bd=0, padx=0, width=1)
            online.place(x=124, y=0, width=52, height=28)
            status = tk.Label(card, font=(fam, -16, "bold"), bg=WINDOW_BG,
                              fg=TEXT_FG, anchor="w", bd=0, padx=0, width=1)
            status.place(x=12, y=29, width=162, height=26)
            tip = Tooltip(light, lambda state=state: t(
                present_station(state["row"], core_connected=state["core_connected"]).text_key))
            Tooltip(icon, lambda state=state: t(
                present_station(state["row"], core_connected=state["core_connected"]).text_key))
            def row_text(state=state):
                pres = present_station(state["row"], core_connected=state["core_connected"])
                return (t("station_label", station=state["station"]) + "\n"
                        + t("row_tip_kvm_unknown" if not state["core_connected"] else
                            ("row_tip_kvm_connected" if pres.online else "row_tip_kvm_disconnected")) + "\n"
                        + connect_failure_tip(state["row"], pres, self._retry_left(state))
                        + format_reading_metrics(state["row"]))
            row_tip = Tooltip(card, row_text)
            Tooltip(title, row_text)
            for widget in (card, *card.winfo_children()):
                widget.bind("<Button-1>", lambda _e, device=dev_id: self._select_device(device))
            widgets = {"card": card, "light": light, "icon": icon, "tip": tip, "row_tip": row_tip,
                       "title": title, "bar": bar, "online_lamp": lamp, "lamp_dot": lamp_dot, "online": online,
                       "status_label": status, "state": state}
            self._station_widgets[dev_id] = widgets
            self._configure_station_row(widgets, present_station(st), core_connected=state["core_connected"])

        self._sync_row_timers()  # the old cards are gone: stop the timers that no row needs any more
        self.update_idletasks()
        self._list_row_tops = {dev: w["card"].winfo_y() for dev, w in self._station_widgets.items()}
        self._list_content_h = self.stations_container.winfo_reqheight()
        self.station_canvas.configure(scrollregion=(0, 0, ROW_W, max(LIST_VIEW_H, self._list_content_h)))
        self._refresh_station_list_marks()
        if len(self._stations) > 12:
            self._log_key("log_list_scroll", level="info", n=len(self._stations))

    def _on_station_list_scrolled(self, _first: str, _last: str) -> None:
        self._refresh_station_list_marks()

    def _refresh_station_list_marks(self) -> None:
        """Redraw the thumb, the red ticks for off-screen abnormal rows and the bottom hint line.

        Never scrolls the list: only the operator does (spec v5.3 §14.10).
        """
        if not hasattr(self, "station_track"):
            return
        track = self.station_track
        track.delete("thumb", "tick")
        content_h = self._list_content_h
        first, last = self.station_canvas.yview()
        visible = last - first
        if content_h > LIST_VIEW_H:
            h = max(THUMB_MIN_H, round(LIST_VIEW_H * LIST_VIEW_H / content_h))
            y = round(first * (LIST_VIEW_H - h) / (1 - visible)) if visible < 1 else 0
            fill = THUMB_ACTIVE if self._track_hover or self._track_drag is not None else THUMB_BG
            track.create_rectangle(2, y, 2 + THUMB_W, y + h, fill=fill, outline="", tags="thumb")
        top = self.station_canvas.canvasy(0)
        self._list_above, self._list_below = [], []
        abnormal_above: List[str] = []
        abnormal_below: List[str] = []
        for dev, row_top in self._list_row_tops.items():
            if row_top >= top and row_top + ROW_H <= top + LIST_VIEW_H:
                continue  # fully visible; a partly visible row counts as off-screen
            above = row_top < top
            (self._list_above if above else self._list_below).append(dev)
            state = self._station_widgets[dev]["state"]
            if present_station(state["row"], core_connected=state["core_connected"]).row_red:
                (abnormal_above if above else abnormal_below).append(dev)
                y = min(LIST_VIEW_H - 6, max(0, (row_top + ROW_H / 2) / content_h * LIST_VIEW_H - 3))
                track.create_rectangle(1, y, 10, y + 6, fill=ALARM_RED, outline="", tags="tick")
        text = list_hint_text(self._list_above, self._list_below, abnormal_above, abnormal_below,
                              lambda candidate: self._list_hint_font.measure(candidate) <= LIST_HINT_MAX_W)
        self.lbl_list_hint.configure(text=text, fg=ALARM_RED if abnormal_above or abnormal_below else HINT_GREY)
        self._alarm_off_screen = (abnormal_above, abnormal_below)
        self._refresh_alarm_banner()

    # --------------------------------------------------------------------------
    # Alarm banner (spec v5.3 §13, §14.4; issue #127)
    # --------------------------------------------------------------------------
    def _build_alarm_banner(self) -> None:
        """Create the banner hidden; ``_refresh_alarm_banner`` packs it under the top bar and unpacks it again."""
        family = self.f_label[0]
        self._banner_main_font = tkfont.Font(root=self, family=family, size=-40, weight="bold")
        self._banner_line_font = tkfont.Font(root=self, family=family, size=-20, weight="bold")
        banner = self.alarm_banner = tk.Frame(self, bg=ALARM_RED, height=BANNER_H)
        banner.pack_propagate(False)
        self.alarm_icon = tk.Canvas(banner, width=BANNER_ICON, height=BANNER_ICON, bg=ALARM_RED, highlightthickness=0)
        self.alarm_icon.pack(side="left", padx=(BANNER_PAD, BANNER_GAP))
        text = tk.Frame(banner, bg=ALARM_RED)
        text.pack(side="left", anchor="w")
        options = dict(bg=ALARM_RED, fg="#ffffff", anchor="w", bd=0, padx=0, pady=0)
        self.lbl_alarm_main = tk.Label(text, font=self._banner_main_font, **options)
        self.lbl_alarm_l2 = tk.Label(text, font=self._banner_line_font, **options)
        self.lbl_alarm_l3 = tk.Label(text, font=self._banner_line_font, **options)
        self.lbl_alarm_main.pack(anchor="w")
        self.lbl_alarm_l2.pack(anchor="w")
        for widget in (banner, self.alarm_icon, text, self.lbl_alarm_main, self.lbl_alarm_l2, self.lbl_alarm_l3):
            widget.bind("<Button-1>", self._on_alarm_banner_click)
        banner.bind("<Configure>", lambda _e: self._refresh_alarm_banner())  # the width decides what fits
        self._banner_count = 0
        self._banner_key: Optional[tuple] = None
        self._banner_redraws = 0
        self._alarm_off_screen: Tuple[List[str], List[str]] = ([], [])

    def _abnormal_devices(self) -> List[Tuple[str, str]]:
        """(device, kind) of every abnormal row in banner order, whether or not it is on screen."""
        found = []
        for dev, widgets in self._station_widgets.items():
            if widgets.get("alarm_kind"):
                found.append((dev, widgets["alarm_kind"]))
        return alarm_banner.sort_abnormal(found)

    def _banner_text_width(self, icons: int) -> int:
        width = self.alarm_banner.winfo_width()
        if width <= 1:
            width = self.winfo_width()
        if width <= 1:
            width = 960
        return width - BANNER_PAD - icons * BANNER_ICON - BANNER_GAP - BANNER_PAD

    def _refresh_alarm_banner(self) -> None:
        """Show, hide or redraw the banner; the content is rebuilt only when what it shows has changed."""
        if not hasattr(self, "alarm_banner"):
            return
        banner = self.alarm_banner
        abnormal = self._abnormal_devices()
        # While Core is down the status is unknown, not cleared: keep the count and log nothing until it is back.
        if self._service_ok is not False and len(abnormal) != self._banner_count:  # one Log line per event
            self._banner_count = len(abnormal)
            self._log_key("log_alarm_shown" if abnormal else "log_alarm_cleared", n=len(abnormal))
        if not abnormal:
            self._banner_key = None
            if banner.winfo_manager():
                banner.pack_forget()
            return
        if not banner.winfo_manager():
            banner.pack(fill="x", after=self.top_frame)
        kinds = [kind for kind in alarm_banner.KINDS if any(k == kind for _d, k in abnormal)]
        shapes = alarm_banner.shapes(kinds)
        width = self._banner_text_width(len(shapes))
        above, below = self._alarm_off_screen
        key = (tuple(abnormal), tuple(above), tuple(below), width, ui_i18n.current_language())
        if key == self._banner_key:
            return
        self._banner_key = key
        self._banner_redraws += 1
        lines = alarm_banner.banner_lines(
            abnormal, above, below,
            lambda text: self._banner_main_font.measure(text) <= width,
            lambda text: self._banner_line_font.measure(text) <= width)
        for label, text in zip((self.lbl_alarm_main, self.lbl_alarm_l2, self.lbl_alarm_l3), lines):
            label.configure(text=text)
        if lines[2]:
            self.lbl_alarm_l3.pack(anchor="w")
        else:
            self.lbl_alarm_l3.pack_forget()
        self.alarm_icon.delete("all")
        self.alarm_icon.configure(width=BANNER_ICON * len(shapes))
        for index, shape in enumerate(shapes):  # one icon per distinct shape present, in category order
            draw = {"■✕": draw_alarm_square_x, "♥": draw_alarm_heart}.get(shape, draw_alarm_triangle)
            draw(self.alarm_icon, BANNER_ICON, x=index * BANNER_ICON, family=self.f_label[0])

    def _on_alarm_banner_click(self, _event: Any = None) -> None:
        """Select the first abnormal device (category order, then list order). Never scrolls the list."""
        abnormal = self._abnormal_devices()
        if abnormal:
            self._select_device(abnormal[0][0])

    def _station_track_press(self, event: Any) -> None:
        thumb = self.station_track.coords("thumb")
        if not thumb:
            return
        if thumb[1] <= event.y <= thumb[3]:
            self._track_drag = event.y - thumb[1]
            self._refresh_station_list_marks()
        else:
            self.station_canvas.yview_scroll(-1 if event.y < thumb[1] else 1, "pages")

    def _station_track_drag(self, event: Any) -> None:
        if self._track_drag is None:
            return
        thumb = self.station_track.coords("thumb")
        if not thumb:
            self._track_drag = None
            return
        h = thumb[3] - thumb[1]
        first, last = self.station_canvas.yview()
        travel = LIST_VIEW_H - h
        self.station_canvas.yview_moveto(max(0, min(travel, event.y - self._track_drag)) / travel * (1 - (last - first)))

    def _station_track_release(self, event: Any) -> None:
        self._track_drag = None
        self._station_track_motion(event)

    def _station_track_motion(self, event: Any) -> None:
        thumb = self.station_track.coords("thumb")
        self._track_hover = bool(event is not None and thumb
                                 and thumb[0] <= event.x <= thumb[2] and thumb[1] <= event.y <= thumb[3])
        self._refresh_station_list_marks()

    def _configure_station_row(self, widgets, pres, *, core_connected: bool = True) -> None:
        """Change row colours and words without changing its geometry."""
        card = widgets["card"]
        card.configure(bg=pres.row_bg)
        for child in card.winfo_children():
            child.configure(bg=pres.row_bg)
        # Red rows are white text throughout; otherwise only the status words carry the status colour.
        text_fg = pres.fg if pres.row_red else TEXT_FG
        widgets["title"].configure(fg=text_fg)
        widgets["online"].configure(fg=text_fg)
        widgets["status_label"].configure(fg=pres.fg)
        widgets["bar"].configure(bg=pres.fg)
        widgets["light"].set_light(pres.light, pres.blink)
        # The ▲ covers the light in the same 16 px box; both stay placed, so a status change never moves a child.
        widgets["row_red"] = pres.row_red  # the alarm banner reads this instead of presenting every row again
        widgets["alarm_kind"] = pres.alarm_kind
        shape = ("heart" if pres.alarm_kind in ("heartbeat", "heartbeat_not_found")
                 else "square_x" if pres.alarm_kind == "connect_failed" else "triangle")
        if widgets.get("icon_kind") != shape:  # redraw the shape inside the same 16 px box; it never moves
            widgets["icon"].delete("all")
            draw = {"heart": draw_alarm_heart, "square_x": draw_alarm_square_x}.get(shape, draw_alarm_triangle)
            draw(widgets["icon"], 16, family=self.f_label[0])
            widgets["icon_kind"] = shape
        stack = "raise" if pres.row_red else "lower"  # widget stacking (Canvas.lift/lower are item methods)
        self.tk.call(stack, widgets["icon"], widgets["light"])
        widgets["row_blink"] = pres.row_blink
        widgets["retry_wait"] = pres.status_key == "station_retry_in"
        if pres.row_blink and not self._row_blink_lit:  # join the shared phase, never restart it
            self._paint_row(widgets, ROW_BLINK_DIM)
        widgets["status_label"].configure(text=ui_i18n.station_status_text(
            pres, self._retry_left(widgets["state"]) or 0))
        self._sync_row_timers()
        widgets["online"].configure(text=t("station_na_lamp" if not core_connected else
                                            ("station_online_lamp" if pres.online else "station_offline_lamp")))
        lamp = widgets["online_lamp"]
        if not core_connected:
            lamp.itemconfigure(widgets["lamp_dot"], fill="",
                               outline="#ffffff" if pres.row_red else "#9E9E9E", width=3, dash=(2, 2))
            return
        lamp.itemconfigure(widgets["lamp_dot"], dash="", fill="#2E7D32" if pres.online else ("" if pres.row_red else "#ffffff"),
                           outline="#ffffff" if pres.row_red else ("#2E7D32" if pres.online else "#9E9E9E"))

    def _retry_left(self, state: Dict[str, Any]) -> Optional[int]:
        """Seconds until the next retry: what Core said at the last poll, counted down; None when Core gave none."""
        base = state.get("retry_base")
        if not base or base[1] is None:
            return None
        return max(0, math.ceil(float(base[1]) - (self._clock() - base[0])))

    @staticmethod
    def _paint_row(widgets: Dict[str, Any], bg: str) -> None:
        """Background of a flashing row: the card and every child except the white left bar."""
        widgets["card"].configure(bg=bg)
        for child in widgets["card"].winfo_children():
            if child is not widgets["bar"] and child.winfo_class() != "Toplevel":  # a Toplevel is a hover tip
                child.configure(bg=bg)

    def _sync_row_timers(self) -> None:
        """Run the shared row timers only while a row needs them (one flashing job, one countdown job)."""
        rows = list(self._station_widgets.values())
        if any(w.get("row_blink") for w in rows):
            if self._row_blink_job is None:
                self._row_blink_job = self.after(500, self._row_blink_tick)
        elif self._row_blink_job is not None:
            self.after_cancel(self._row_blink_job)
            self._row_blink_job = None
            self._row_blink_lit = True
        if any(w.get("retry_wait") for w in rows):
            if self._retry_countdown_job is None:
                self._retry_countdown_job = self.after(1000, self._retry_countdown_tick)
        elif self._retry_countdown_job is not None:
            self.after_cancel(self._retry_countdown_job)
            self._retry_countdown_job = None

    def _row_blink_tick(self) -> None:
        """Flip every Connection failure row between #B71C1C and the dim red together (spec v5.2 §4.1)."""
        if self._row_blink_job is not None:
            self.after_cancel(self._row_blink_job)
        self._row_blink_job = None
        if self._is_closing:
            return
        self._row_blink_lit = not self._row_blink_lit
        for w in self._station_widgets.values():
            if w.get("row_blink") and w["card"].winfo_exists():
                self._paint_row(w, "#B71C1C" if self._row_blink_lit else ROW_BLINK_DIM)
        self._sync_row_timers()

    def _retry_countdown_tick(self) -> None:
        """Count the Retry words down once per second until the next poll replaces them."""
        if self._retry_countdown_job is not None:
            self.after_cancel(self._retry_countdown_job)
        self._retry_countdown_job = None
        if self._is_closing:
            return
        for w in self._station_widgets.values():
            if w.get("retry_wait") and w["card"].winfo_exists():
                pres = present_station(w["state"]["row"], core_connected=w["state"]["core_connected"])
                w["status_label"].configure(text=ui_i18n.station_status_text(
                    pres, self._retry_left(w["state"]) or 0))
        self._sync_row_timers()

    def _empty_hint_key(self) -> str:
        """Why the dashboard is empty: not connected (nothing to show yet) or connected with no machines."""
        return "dash_empty" if self._service_ok else "dash_offline"

    def _render_empty_hint(self) -> None:
        """Hint in place of a blank dashboard (issues #115, #140). Shown only when there are no machines.

        Offline: two short lines, the first red, with the full reason, the address and who to call in a hover tip.
        Connected with no machines: one grey line.
        """
        if getattr(self, "empty_hint_box", None) is not None:
            self.empty_hint_box.destroy()
        self.empty_hint_box = self.lbl_empty_hint = None
        self.empty_hint_labels: List[tk.Label] = []
        self.empty_hint_tips: List[Tooltip] = []
        self._empty_hint_shown = None
        if self._stations:
            return
        key = self._empty_hint_key()
        lines = ((("dash_offline_title", OFFLINE_FG), ("dash_offline_action", TEXT_FG)) if key == "dash_offline"
                 else ((key, MUTED_FG),))
        box = tk.Frame(self.station_panel, bg=WINDOW_BG)
        for line_key, fg in lines:
            label = tk.Label(box, font=self.f_label, fg=fg, bg=WINDOW_BG, anchor="w", justify="left")
            bind(label, line_key)
            # The list column is a fixed 200 px at every scaling, but a width in characters grows with the font:
            # fit each line to 180 px and wrap there, so the whole text stays inside the column (Windows CI, #137).
            fit_label_to_pixels(label, 180)
            label.pack(fill="x")
            self.empty_hint_labels.append(label)
            if key == "dash_offline":
                self.empty_hint_tips.append(Tooltip(label, self._offline_tip_text))
        box.pack(fill="x", pady=8, before=self.station_list_frame)
        self.empty_hint_box = box
        self.lbl_empty_hint = self.empty_hint_labels[0]
        self._empty_hint_shown = key

    def _offline_tip_text(self, lang: Optional[str] = None) -> str:
        """Hover tip of the offline hint: the full reason, the address it tries, and who to call (#140, §14.14)."""
        addr = f"{self.client.host}:{self.client.port}"
        return "\n".join((t("dash_offline", lang), t("dash_offline_addr", lang, addr=addr), t("dash_offline_call", lang)))

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
        for w in self._station_widgets.values():
            self._configure_station_row(w, present_station(
                w["state"]["row"], core_connected=w["state"]["core_connected"]),
                core_connected=w["state"]["core_connected"])
        self._refresh_station_list_marks()
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
            if isinstance(stations, list):
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
                        pres = present_station(st)
                        w["state"]["row"] = dict(st)
                        w["state"]["retry_base"] = (self._clock(), st.get("retry_in_sec"))
                        w["state"]["core_connected"] = True
                        self._configure_station_row(w, pres, core_connected=w["state"]["core_connected"])
                        w["state"]["status"] = str(st.get("status", "DISCONNECTED")).upper()
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
                # §14.14: the explanation behind the short offline hint goes into the Log too, on one line.
                self._append_log(*(self._offline_tip_text(lang).replace("\n", " ") for lang in ("en", "zh")), level="warn")
            for w in self._station_widgets.values():
                pres = present_station(w["state"]["row"], core_connected=False)
                w["state"]["core_connected"] = False
                self._configure_station_row(w, pres, core_connected=False)
                w["state"]["status"] = "DISCONNECTED"
        self._refresh_station_list_marks()
        if not self._stations and self._empty_hint_shown != self._empty_hint_key():
            self._render_empty_hint()  # offline <-> connected: a different hint, not only different words

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
        bind_clamped(label, lambda: f"{t(brief_key)}{t('see_log')}")
        label.config(fg=fg, cursor="hand2")
        label.bind("<Button-1>", lambda _event, m=mark: self._jump_to_log(m))
        return mark

    @staticmethod
    def _set_info(label: tk.Label, text: Any, fg: str) -> None:
        """Plain (non-error) label text. A callable is re-run on a language switch; clears any click-to-log binding."""
        render = text if callable(text) else (lambda: text)
        bind_clamped(label, render)
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
            res = restart_core_daemon(host=self.client.host, port=self.client.port, timeout=CORE_START_TIMEOUT_S)
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


# A cold Core.exe start (imports + antivirus scan of a fresh bundle) can take longer than 10 s (#167).
CORE_START_TIMEOUT_S = 30.0


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
    # Issue #115: the logo and the bridge icon must be inside the frozen exe, and CI must have written the build info.
    for name in ("foxlink_logo.png", "bridge_16.png", "bridge_32.png", "bridge_48.png"):
        if not asset_path(name).is_file():
            problems.append(f"asset missing: {asset_path(name)}")
    if getattr(sys, "frozen", False) and not is_ci_build():
        problems.append("_build_info.py is missing from the exe (tools/write_build_info.py did not run)")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version()):
        problems.append(f"version is not x.y.z: {version()!r}")
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
    parser.add_argument("--restart-core", action="store_true", help="Restart Core without opening a window")
    args = parser.parse_args()

    if args.restart_core:
        from core_daemon import restart_core_daemon, send_tcp_command
        try:
            result = restart_core_daemon(host=args.host, port=args.port, timeout=CORE_START_TIMEOUT_S)
            reply = send_tcp_command("ping", host=args.host, port=args.port)
            sys.exit(0 if result.get("status") == "ok" and reply.get("data", {}).get("pong") is True else 1)
        except Exception:
            sys.exit(1)

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
