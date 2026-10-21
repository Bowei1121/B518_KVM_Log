"""Monitor UI device settings page: edit the device list file as a table (ticket #10).

All rules live in device_list_editor; this module only lays them out. The page reads and
writes the local device list, so it works when Core Service is not running.
"""

import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import tkinter as tk
from tkinter import ttk

import device_list_editor as editor
from device_registry import default_registry_path
from ui_widgets import BUTTON_BG, ENTRY_BG, ERROR_FG, MUTED_FG, OK_FG, TEXT_FG, WINDOW_BG

STATIONS = ("DFU", "FCT", "BT")
ERROR_COLOR = ERROR_FG
OK_COLOR = OK_FG


class DeviceSettingsWindow(tk.Toplevel):
    def __init__(self, parent: Any, client: Any, path: Optional[Path] = None,
                 restart_core: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        super().__init__(parent)
        self.title("設備設定")
        self.geometry("880x520")
        self.configure(bg=WINDOW_BG)
        self._client = client
        self._path = Path(path) if path is not None else default_registry_path()
        self._restart_core = restart_core
        self._rows: List[Dict[str, Any]] = []  # each: vars, widgets, probe label, error label
        self._saved_snapshot: List[Dict[str, Any]] = []
        self._errors: Dict[Any, List[str]] = {}
        self._posted: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._dirty = False

        self._build()
        self._load_from_file()
        self.after(100, self._drain)

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        head = tk.Frame(self, padx=10, pady=6, bg=WINDOW_BG)
        head.pack(fill="x")
        tk.Label(head, text=f"設備清單檔: {self._path}", font=("Arial", 10), bg=WINDOW_BG, fg=TEXT_FG).pack(anchor="w")
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

        for col, title in enumerate(("選取", "設備 ID", "站別", "KVM IP", "測試通道數", "連線測試")):
            tk.Label(self.table, text=title, font=("Arial", 10, "bold"), bg=WINDOW_BG, fg=TEXT_FG).grid(row=0, column=col, padx=4, sticky="w")

        bar = tk.Frame(self, padx=10, pady=8, bg=WINDOW_BG)
        bar.pack(fill="x")
        buttons = (
            ("➕ 新增設備", self._add_row),
            ("🗑 刪除選取", self._delete_selected),
            ("🔌 全部測試", self._probe_all),
            ("↺ 還原", self._restore),
            ("💾 儲存", self._save),
            ("🔄 重啟 Core", self._restart),
        )
        for text, command in buttons:
            tk.Button(bar, text=text, command=command, font=("Arial", 10), padx=8, pady=3,
                      bg=BUTTON_BG, fg=TEXT_FG).pack(side="left", padx=4)

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
            self._status("尚無設備清單，已預填 B518 九台（通道數 4）。按「儲存」後生效，需重啟 Core。", OK_COLOR)
        else:
            self._status(f"已載入 {len(state.rows)} 台設備。修改後按「儲存」，需重啟 Core 才會生效。", MUTED_FG)
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
                                 "widgets": []}
        probe_btn = tk.Button(self.table, text="測試", command=lambda: self._probe_row(entry), bg=BUTTON_BG, fg=TEXT_FG)

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
            self._status("沒有選取要刪除的列。", MUTED_FG)
            return
        rows = [{key: var.get() for key, var in entry["vars"].items()} for entry in keep]
        self._clear_rows()
        for row in rows:
            self._add_row_widgets(row)
        self._show_errors({})
        self._status(f"已刪除 {len(dropped)} 列（尚未儲存）。", MUTED_FG)

    def _restore(self) -> None:
        self._load_from_file()
        self._status("已還原為檔案內容。", MUTED_FG)

    # ------------------------------------------------------------ validation
    def _show_errors(self, errors: Dict[Any, List[str]]) -> None:
        self._errors = errors
        for index, entry in enumerate(self._rows):
            messages = errors.get(index, [])
            entry["error"].config(text=("⚠ " + "；".join(messages)) if messages else "")
        form = errors.get(editor.FORM_ERROR, [])
        if form:
            self._status("；".join(form), ERROR_COLOR)

    def _save(self) -> None:
        rows = self._current_rows()
        errors = editor.validate_rows(rows)
        self._show_errors(errors)
        if errors:
            count = len([k for k in errors if k != editor.FORM_ERROR])
            self._status(f"未儲存：{count} 列有錯誤，請依列內提示修正。", ERROR_COLOR)
            return
        try:
            backup = editor.save_rows(rows, self._path)
        except (editor.SaveRefused, OSError) as exc:
            self._status(f"未儲存: {exc}", ERROR_COLOR)
            return
        self._saved_snapshot = self._snapshot()
        self._update_dirty()
        note = f"已儲存（備份: {backup.name}）。需重啟 Core 才會生效。" if backup else "已儲存。需重啟 Core 才會生效。"
        self._status(note, OK_COLOR)

    # -------------------------------------------------------------- probing
    def _probe_row(self, entry: Dict[str, Any]) -> None:
        ip = entry["vars"]["kvm_ip"].get().strip()
        entry["probe"].config(text="測試中…", fg=MUTED_FG)

        def worker() -> None:
            result = editor.probe_kvm(ip)
            self._post(lambda: self._show_probe(entry, result))

        threading.Thread(target=worker, daemon=True).start()

    def _show_probe(self, entry: Dict[str, Any], result: Any) -> None:
        if not any(live is entry for live in self._rows):
            return  # the row was deleted or rebuilt while the probe ran; its widgets are gone
        entry["probe"].config(text=result.message, fg=OK_COLOR if result.ok else ERROR_COLOR)

    def _probe_all(self) -> None:
        for entry in list(self._rows):
            self._probe_row(entry)
        self._status(f"已送出 {len(self._rows)} 台連線測試。", MUTED_FG)

    # -------------------------------------------------------------- restart
    def _restart(self) -> None:
        if self.dirty:
            self._status("有未儲存的修改。請先儲存，再重啟 Core。", ERROR_COLOR)
            return
        self._status("檢查設備狀態…", MUTED_FG)

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
                self._post(lambda: self._status("此視窗未提供重啟功能。", ERROR_COLOR))
                return
            self._post(lambda: self._status("重啟 Core 中…", MUTED_FG))
            result = self._restart_core()
            ok = bool(result.get("status") == "ok" or result.get("success"))
            message = "Core 已重啟。" if ok else f"重啟失敗: {result.get('message', result)}"
            self._post(lambda: self._status(message, OK_COLOR if ok else ERROR_COLOR))

        threading.Thread(target=worker, daemon=True).start()

    def _status(self, text: str, color: str) -> None:
        self.lbl_status.config(text=text, fg=color)

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
