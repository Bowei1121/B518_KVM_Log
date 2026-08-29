# -*- coding: utf-8 -*-
"""
即時串流視窗 (Streamer) + 互動操作
====================================
- 視窗可縮放, 影像等比例填滿 (letterbox)。
- 滑鼠: 在畫面上移動/點擊(左中右)/拖曳/滾輪 -> 直接操作遠端。
- 鍵盤: 視窗有焦點時, 按鍵直接送到遠端 (含 Ctrl/Shift/Alt 修飾鍵)。

input_sender(method, params): 由呼叫端提供, 負責把 JSON-RPC 指令送到遠端
(且要 marshal 到 KVM 的事件迴圈執行緒)。None 則為唯讀串流。
座標: 顯示座標 -> 影格像素 -> 0..32767 (absMouseReport)。
"""
import time
import tkinter as tk

import cv2
from PIL import Image, ImageTk

# Tkinter keysym -> USB HID usage code
_KEYSYM_HID = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _KEYSYM_HID[_c] = 0x04 + _i
    _KEYSYM_HID[_c.upper()] = 0x04 + _i
for _i, _name in enumerate(["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]):
    _KEYSYM_HID[_name] = 0x1E + _i
# shifted 數字符號 -> 對應數字鍵 (Shift 由修飾鍵另外送)
for _sym, _base in {"exclam": "1", "at": "2", "numbersign": "3", "dollar": "4",
                    "percent": "5", "asciicircum": "6", "ampersand": "7",
                    "asterisk": "8", "parenleft": "9", "parenright": "0"}.items():
    _KEYSYM_HID[_sym] = _KEYSYM_HID[_base]
_KEYSYM_HID.update({
    "Return": 0x28, "KP_Enter": 0x28, "Escape": 0x29, "BackSpace": 0x2A,
    "Tab": 0x2B, "space": 0x2C, "minus": 0x2D, "underscore": 0x2D,
    "equal": 0x2E, "plus": 0x2E, "bracketleft": 0x2F, "braceleft": 0x2F,
    "bracketright": 0x30, "braceright": 0x30, "backslash": 0x31, "bar": 0x31,
    "semicolon": 0x33, "colon": 0x33, "apostrophe": 0x34, "quotedbl": 0x34,
    "grave": 0x35, "asciitilde": 0x35, "comma": 0x36, "less": 0x36,
    "period": 0x37, "greater": 0x37, "slash": 0x38, "question": 0x38,
    "Caps_Lock": 0x39,
    "F1": 0x3A, "F2": 0x3B, "F3": 0x3C, "F4": 0x3D, "F5": 0x3E, "F6": 0x3F,
    "F7": 0x40, "F8": 0x41, "F9": 0x42, "F10": 0x43, "F11": 0x44, "F12": 0x45,
    "Insert": 0x49, "Home": 0x4A, "Prior": 0x4B, "Delete": 0x4C, "End": 0x4D,
    "Next": 0x4E, "Right": 0x4F, "Left": 0x50, "Down": 0x51, "Up": 0x52,
})

# 修飾鍵 keysym -> HID modifier bit (左右合併到 HID 的左側位元, 對遠端等效)
_MOD_BIT = {
    "Control_L": 0x01, "Control_R": 0x01,
    "Shift_L": 0x02, "Shift_R": 0x02,
    "Alt_L": 0x04, "Alt_R": 0x04,
    "Super_L": 0x08, "Super_R": 0x08,
}


class StreamerWindow(tk.Toplevel):
    def __init__(self, parent, frame_getter, input_sender=None, fps=30,
                 title="Streamer", on_close=None):
        super().__init__(parent)
        self.title(title)
        self.frame_getter = frame_getter
        self.input_sender = input_sender
        self.on_close = on_close
        self._interval = max(1, int(1000 / fps))
        self._after_id = None
        self._photo = None
        self._closed = False
        self._map = None              # (scale, off_x, off_y, frame_w, frame_h)
        self._buttons = 0             # 目前滑鼠按鍵 bitmask
        self._last_move = 0.0
        self._mods = 0                # 目前修飾鍵 bitmask
        self._keys = []               # 目前按住的一般鍵 usage (最多 6)

        self.geometry("1280x760")
        self.resizable(True, True)

        self.label = tk.Label(self, bg="black")
        self.label.pack(fill="both", expand=True)
        self.status = tk.Label(self, anchor="w",
                               text="串流中 (點畫面操作遠端; 滑鼠移動/點擊/滾輪 + 鍵盤)")
        self.status.pack(fill="x")

        if self.input_sender:
            self.label.bind("<Motion>", self._on_motion)
            self.label.bind("<Button-1>", lambda e: self._on_btn(e, 1, True))
            self.label.bind("<ButtonRelease-1>", lambda e: self._on_btn(e, 1, False))
            self.label.bind("<Button-3>", lambda e: self._on_btn(e, 2, True))
            self.label.bind("<ButtonRelease-3>", lambda e: self._on_btn(e, 2, False))
            self.label.bind("<Button-2>", lambda e: self._on_btn(e, 4, True))
            self.label.bind("<ButtonRelease-2>", lambda e: self._on_btn(e, 4, False))
            self.label.bind("<MouseWheel>", self._on_wheel)
            self.bind("<KeyPress>", self._on_key_press)
            self.bind("<KeyRelease>", self._on_key_release)
            # 失去焦點時放開所有鍵 (Alt 切選單 / Alt-Tab 等會遺失放開 -> 卡鍵)
            self.bind("<FocusOut>", self._on_focus_out)
            # 滑鼠進入畫面就取得鍵盤焦點, 讓打字直接送到遠端
            self.label.bind("<Enter>", lambda e: self.focus_set())

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.focus_set()
        self._tick()

    # ---- 畫面更新 ----
    def _tick(self):
        if self._closed:
            return
        frame = None
        try:
            frame = self.frame_getter()
        except Exception:
            frame = None
        if frame is not None:
            h, w = frame.shape[:2]
            lw = max(self.label.winfo_width(), 1)
            lh = max(self.label.winfo_height(), 1)
            scale = min(lw / w, lh / h)
            dw, dh = max(1, int(w * scale)), max(1, int(h * scale))
            disp = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
            self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
            self.label.config(image=self._photo)
            # 影像在 label 內置中, 記錄縮放與偏移供座標換算
            self._map = (scale, (lw - dw) // 2, (lh - dh) // 2, w, h)
        self._after_id = self.after(self._interval, self._tick)

    # ---- 座標換算 ----
    def _to_abs(self, ex, ey):
        if not self._map:
            return None
        scale, ox, oy, w, h = self._map
        px = (ex - ox) / scale
        py = (ey - oy) / scale
        px = min(max(px, 0), w - 1)
        py = min(max(py, 0), h - 1)
        return round(px / (w - 1) * 32767), round(py / (h - 1) * 32767)

    def _send_mouse(self, ex, ey):
        a = self._to_abs(ex, ey)
        if a:
            self.input_sender("absMouseReport",
                              {"x": a[0], "y": a[1], "buttons": self._buttons})

    # ---- 滑鼠 ----
    def _on_motion(self, e):
        now = time.monotonic()
        if now - self._last_move < 0.016:    # 節流 ~60/s
            return
        self._last_move = now
        self._sync_mods(e.state)             # 順便校正卡住的修飾鍵
        self._send_mouse(e.x, e.y)

    def _on_btn(self, e, bit, press):
        self.focus_set()
        self._sync_mods(e.state)             # 點擊前校正修飾鍵 (避免 Shift 卡住變 shift+click)
        if press:
            self._buttons |= bit
            self._send_mouse(e.x, e.y)
        else:
            self._buttons &= ~bit
            self._send_mouse(e.x, e.y)
            # 保險: 稍後補送一次放開狀態, 避免放開訊號被吞 -> 卡鍵。
            # 但若期間又按下新鍵 (buttons != 0) 則略過, 以免誤放開。
            ex, ey = e.x, e.y
            self.after(40, lambda: self._buttons == 0 and self._send_mouse(ex, ey))

    def _on_wheel(self, e):
        step = int(e.delta / 120) or (1 if e.delta > 0 else -1)
        self.input_sender("wheelReport", {"wheelY": step})

    # ---- 鍵盤 (韌體 0.4.6 用 keyboardReport) ----
    # 策略: Shift(0x02)/Ctrl(0x01) 以 e.state 校正 (位元可靠, 對遺失放開免疫);
    #       Alt(0x04)/Win(0x08) 靠按鍵追蹤 + 失焦時全放開 (Alt 是 Windows 系統鍵,
    #       常因切選單/Alt-Tab 而遺失放開, e.state 的 Alt 位元又不可靠)。
    @staticmethod
    def _shift_ctrl_from_state(state):
        m = 0
        if state & 0x0001:   # Shift
            m |= 0x02
        if state & 0x0004:   # Control
            m |= 0x01
        return m

    def _send_kbd(self):
        self.input_sender("keyboardReport", {"modifier": self._mods, "keys": list(self._keys)})

    def _merge_mods(self, state):
        """Shift/Ctrl 取自 state; Alt/Win (0x04/0x08) 沿用目前追蹤值。"""
        return self._shift_ctrl_from_state(state) | (self._mods & 0x0C)

    def _sync_mods(self, state):
        """滑鼠事件時校正 Shift/Ctrl (清掉卡住的 Shift)。"""
        m = self._merge_mods(state)
        if m != self._mods:
            self._mods = m
            self._send_kbd()

    def _on_key_press(self, e):
        m = self._merge_mods(e.state)
        bit = _MOD_BIT.get(e.keysym)
        if bit is not None:
            m |= bit                 # 按下修飾鍵
        else:
            usage = _KEYSYM_HID.get(e.keysym)
            if usage and usage not in self._keys and len(self._keys) < 6:
                self._keys.append(usage)
        self._mods = m
        self._send_kbd()

    def _on_key_release(self, e):
        m = self._merge_mods(e.state)
        bit = _MOD_BIT.get(e.keysym)
        if bit is not None:
            m &= ~bit                # 放開修飾鍵
        else:
            usage = _KEYSYM_HID.get(e.keysym)
            if usage and usage in self._keys:
                self._keys.remove(usage)
        self._mods = m
        self._send_kbd()

    def _on_focus_out(self, e):
        """失去焦點: 放開所有修飾鍵與按鍵 (避免 Alt/Ctrl 等卡住)。"""
        if self._mods or self._keys:
            self._mods = 0
            self._keys = []
            self._send_kbd()

    def close(self):
        self._closed = True
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass
        self.destroy()
