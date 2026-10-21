"""Monitor UI device settings page: edit the device list file as a table (ticket #10).

All rules live in device_list_editor; this module only lays them out. The page reads and
writes the local device list, so it works when Core Service is not running.
"""

import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import tkinter as tk
from tkinter import ttk

import device_list_editor as editor
from device_registry import default_registry_path
import ui_i18n
from ui_i18n import bind, t
from ui_widgets import BUTTON_BG, ENTRY_BG, ERROR_FG, MUTED_FG, OK_FG, TEXT_FG, WINDOW_BG, Tooltip, pin_light_appearance

STATIONS = ("DFU", "FCT", "BT")
ERROR_COLOR = ERROR_FG
OK_COLOR = OK_FG


class DeviceSettingsWindow(tk.Toplevel):
    def __init__(self, parent: Any, client: Any, path: Optional[Path] = None,
                 restart_core: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        super().__init__(parent)
        bind(self, "ds_title", option="title")
        self.geometry("880x520")
        self.configure(bg=WINDOW_BG)
        pin_light_appearance(self)
        self._client = client
        self._path = Path(path) if path is not None else default_registry_path()
        self._restart_core = restart_core
        self._rows: List[Dict[str, Any]] = []  # each: vars, widgets, probe label, error label
        self._saved_snapshot: List[Dict[str, Any]] = []
        self._errors: Dict[Any, List[str]] = {}
        self._status_state: Optional[tuple] = None  # (render, colour): the status line, re-worded on a language switch
        self._posted: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._dirty = False

        self._build()
        self._load_from_file()
        self.after(100, self._drain)
        ui_i18n.on_language_change(self._relabel)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event: Any) -> None:
        if event.widget is self:
            ui_i18n.off_language_change(self._relabel)

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        head = tk.Frame(self, padx=10, pady=6, bg=WINDOW_BG)
        head.pack(fill="x")
        bind(tk.Label(head, font=("Arial", 10), bg=WINDOW_BG, fg=TEXT_FG), "ds_path", path=str(self._path)).pack(anchor="w")
        self.lbl_status = tk.Label(head, text="", font=("Arial", 10), anchor="w", justify="left", wraplength=840,
                                   bg=WINDOW_BG, fg=TEXT_FG)
        self.lbl_status.pack(fill="x", pady=(4, 0))

        table_frame = tk.Frame(self, padx=10, bg=WINDOW_BG)
        table_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(table_frame, highlightthickness=0, bg=WINDOW_BG)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=canvas.yview)
        self.table = tk.Frame(canvas, bg=WINDOW_BG)
        self.table.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.table, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        headers = ("ds_h_select", "ds_h_device", "ds_h_station", "ds_h_ip", "ds_h_channels", "ds_h_probe")
        for col, key in enumerate(headers):
            bind(tk.Label(self.table, font=("Arial", 10, "bold"), bg=WINDOW_BG, fg=TEXT_FG), key).grid(
                row=0, column=col, padx=4, sticky="w")

        bar = tk.Frame(self, padx=10, pady=8, bg=WINDOW_BG)
        bar.pack(fill="x")
        buttons = (
            ("ds_add", "tip_ds_add", self._add_row),
            ("ds_delete", "tip_ds_delete", self._delete_selected),
            ("ds_test_all", "tip_ds_test_all", self._probe_all),
            ("ds_revert", "tip_ds_revert", self._restore),
            ("ds_save", "tip_ds_save", self._save),
            ("ds_restart", "tip_ds_restart", self._restart),
        )
        for key, tip, command in buttons:
            button = bind(tk.Button(bar, command=command, font=("Arial", 10), padx=8, pady=3,
                                    bg=BUTTON_BG, fg=TEXT_FG), key)
            button.pack(side="left", padx=4)
            Tooltip(button, lambda tip=tip: t(tip))

    # ------------------------------------------------------------------ rows
    def _load_from_file(self) -> None:
        state = editor.load_state(self._path)
        self._clear_rows()
        for row in state.rows:
            self._add_row_widgets(row)
        self._saved_snapshot = self._snapshot()
        self._update_dirty()
        self._errors = {}
        if state.load_error:
            self._status(state.load_error, ERROR_COLOR)
        elif state.prefilled:
            self._say("ds_prefilled", OK_COLOR)
        else:
            self._say("ds_loaded", MUTED_FG, count=len(state.rows))
        self._show_errors({})

    def _clear_rows(self) -> None:
        for entry in self._rows:
            for widget in entry["widgets"]:
                widget.destroy()
        self._rows = []

    def _add_row(self) -> None:
        self._add_row_widgets({"device": "", "station": "DFU", "kvm_ip": "", "channels": 4})
        self._show_errors({})

    def _add_row_widgets(self, row: Dict[str, Any]) -> None:
        index = len(self._rows)
        r = index + 1
        selected = tk.BooleanVar(value=False)
        device = tk.StringVar(value=str(row.get("device", "")))
        station = tk.StringVar(value=str(row.get("station", "")))
        ip = tk.StringVar(value=str(row.get("kvm_ip", "")))
        channels = tk.StringVar(value=str(row.get("channels", "")))

        check = tk.Checkbutton(self.table, variable=selected, bg=WINDOW_BG, fg=TEXT_FG, activebackground=WINDOW_BG)
        e_device = tk.Entry(self.table, textvariable=device, width=10, font=("Arial", 10), bg=ENTRY_BG, fg=TEXT_FG)
        combo = ttk.Combobox(self.table, textvariable=station, values=list(STATIONS), state="readonly", width=6)
        e_ip = tk.Entry(self.table, textvariable=ip, width=16, font=("Arial", 10), bg=ENTRY_BG, fg=TEXT_FG)
        numeric = (self.register(lambda text: text == "" or text.isdigit()), "%P")
        e_channels = tk.Entry(self.table, textvariable=channels, width=8, font=("Arial", 10),
                              validate="key", validatecommand=numeric, bg=ENTRY_BG, fg=TEXT_FG)
        probe_lbl = tk.Label(self.table, text="", font=("Arial", 9), fg=MUTED_FG, bg=WINDOW_BG, width=18, anchor="w")
        error_lbl = tk.Label(self.table, text="", font=("Arial", 9), fg=ERROR_COLOR, bg=WINDOW_BG, anchor="w", justify="left")

        vars_ = {"device": device, "station": station, "kvm_ip": ip, "channels": channels}
        # The probe is bound to this entry object, not to its position in self._rows.
        entry: Dict[str, Any] = {"selected": selected, "vars": vars_, "probe": probe_lbl, "error": error_lbl,
                                 "widgets": [], "probe_state": None}
        probe_btn = bind(tk.Button(self.table, command=lambda: self._probe_row(entry), bg=BUTTON_BG, fg=TEXT_FG),
                         "ds_probe_btn")

        Tooltip(check, lambda: t("tip_ds_select"))
        Tooltip(e_device, lambda: t("tip_ds_device"))
        Tooltip(combo, lambda: t("tip_ds_station"))
        Tooltip(e_ip, lambda: t("tip_ds_ip"))
        Tooltip(e_channels, lambda: t("tip_ds_channels"))
        Tooltip(probe_btn, lambda: t("tip_ds_probe"))

        for col, widget in enumerate((check, e_device, combo, e_ip, e_channels, probe_btn), start=0):
            widget.grid(row=r * 2 - 1, column=col, padx=4, pady=2, sticky="w")
        probe_lbl.grid(row=r * 2 - 1, column=6, sticky="w")
        error_lbl.grid(row=r * 2, column=1, columnspan=6, padx=4, sticky="w")

        entry["widgets"] = [check, e_device, combo, e_ip, e_channels, probe_btn, probe_lbl, error_lbl]
        self._rows.append(entry)
        for var in vars_.values():
            var.trace_add("write", lambda *_a: self._update_dirty())
        self._update_dirty()

    def _current_rows(self) -> List[Dict[str, Any]]:
        return [{key: var.get() for key, var in entry["vars"].items()} for entry in self._rows]

    def _snapshot(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self._current_rows()]

    def _update_dirty(self) -> None:
        if not self._saved_snapshot and not self._rows:
            return
        dirty = self._current_rows() != self._saved_snapshot
        self._dirty = dirty

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _delete_selected(self) -> None:
        keep = [entry for entry in self._rows if not entry["selected"].get()]
        dropped = [entry for entry in self._rows if entry["selected"].get()]
        if not dropped:
            self._say("ds_nothing_selected", MUTED_FG)
            return
        rows = [{key: var.get() for key, var in entry["vars"].items()} for entry in keep]
        self._clear_rows()
        for row in rows:
            self._add_row_widgets(row)
        self._show_errors({})
        self._say("ds_deleted", MUTED_FG, count=len(dropped))

    def _restore(self) -> None:
        self._load_from_file()
        self._say("ds_reverted", MUTED_FG)

    # ------------------------------------------------------------ validation
    def _show_errors(self, errors: Dict[Any, List[str]]) -> None:
        self._errors = errors
        for index, entry in enumerate(self._rows):
            messages = errors.get(index, [])
            entry["error"].config(text=("⚠ " + t("sep_clause").join(messages)) if messages else "")
        form = errors.get(editor.FORM_ERROR, [])
        if form:
            self._status(t("sep_clause").join(form), ERROR_COLOR)

    def _save(self) -> None:
        rows = self._current_rows()
        errors = editor.validate_rows(rows)
        self._show_errors(errors)
        if errors:
            count = len([k for k in errors if k != editor.FORM_ERROR])
            self._say("ds_has_errors", ERROR_COLOR, count=count)
            return
        try:
            backup = editor.save_rows(rows, self._path)
        except (editor.SaveRefused, OSError) as exc:
            self._status(t("ds_not_saved", error=exc), ERROR_COLOR)
            return
        self._saved_snapshot = self._snapshot()
        self._update_dirty()
        if backup:
            self._say("ds_saved_backup", OK_COLOR, name=backup.name)
        else:
            self._say("ds_saved", OK_COLOR)

    # -------------------------------------------------------------- probing
    def _probe_row(self, entry: Dict[str, Any]) -> None:
        ip = entry["vars"]["kvm_ip"].get().strip()
        entry["probe_state"] = "probing"
        self._paint_probe(entry)

        def worker() -> None:
            result = editor.probe_kvm(ip)
            self._post(lambda: self._show_probe(entry, result))

        threading.Thread(target=worker, daemon=True).start()

    def _show_probe(self, entry: Dict[str, Any], result: Any) -> None:
        if not any(live is entry for live in self._rows):
            return  # the row was deleted or rebuilt while the probe ran; its widgets are gone
        entry["probe_state"] = result
        self._paint_probe(entry)

    def _paint_probe(self, entry: Dict[str, Any]) -> None:
        """Show a row's probe state in the current language; called again on every language switch."""
        state = entry["probe_state"]
        if state == "probing":
            entry["probe"].config(text=t("ds_probing"), fg=MUTED_FG)
        elif state is not None:
            entry["probe"].config(text=state.text(), fg=OK_COLOR if state.ok else ERROR_COLOR)

    def _probe_all(self) -> None:
        for entry in list(self._rows):
            self._probe_row(entry)
        self._say("ds_probe_started", MUTED_FG, count=len(self._rows))

    # -------------------------------------------------------------- restart
    def _restart(self) -> None:
        if self.dirty:
            self._say("ds_dirty", ERROR_COLOR)
            return
        self._say("ds_checking", MUTED_FG)

        def worker() -> None:
            try:
                status = self._client.get_status(timeout=2.0)
            except Exception:
                status = {"status": "error"}
            connected = status.get("status") == "ok"
            refusal = editor.restart_refusal(status.get("stations") or []) if connected else None
            if refusal:
                self._post(lambda: self._status(refusal, ERROR_COLOR))
                return
            if self._restart_core is None:
                self._post(lambda: self._say("ds_no_restart", ERROR_COLOR))
                return
            self._post(lambda: self._say("ds_restarting", MUTED_FG))
            result = self._restart_core()
            ok = bool(result.get("status") == "ok" or result.get("success"))
            if ok:
                self._post(lambda: self._say("ds_restarted", OK_COLOR))
            else:
                message = str(result.get("message", t("err_restart")))
                self._post(lambda: self._status(message, ERROR_COLOR))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------- status & language
    def _status(self, text: Union[str, Callable[[], str]], color: str) -> None:
        """Set the status line. A callable is kept and re-run on a language switch; a plain string stays as it is."""
        render = text if callable(text) else (lambda: text)
        self._status_state = (render, color)
        self.lbl_status.config(text=render(), fg=color)

    def _say(self, key: str, color: str, **kwargs: Any) -> None:
        self._status(lambda: t(key, **kwargs), color)

    def _relabel(self) -> None:
        """Language switch: re-word the status line, the row messages and the row errors."""
        if not self.winfo_exists():
            return
        if self._errors:
            # Validation messages are built in the current language; validate again to get them in the new one.
            self._show_errors(editor.validate_rows(self._current_rows()))
        for entry in self._rows:
            self._paint_probe(entry)
        if self._status_state is not None and not self._errors.get(editor.FORM_ERROR):
            render, color = self._status_state
            self.lbl_status.config(text=render(), fg=color)

    def _post(self, fn: Callable[[], None]) -> None:
        """Worker threads hand UI updates to the Tk thread through a queue."""
        self._posted.put(fn)

    def _drain(self) -> None:
        try:
            while True:
                self._posted.get_nowait()()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain)
