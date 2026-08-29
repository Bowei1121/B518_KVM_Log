# -*- coding: utf-8 -*-
"""
Mac 滑鼠/鍵盤 測試小工具
=========================
在 macOS 本機執行, 控制「這台 Mac 自己」的滑鼠與鍵盤:
  1. 輸入 X/Y 座標 -> 移動 / 點擊 / 雙擊滑鼠
  2. 輸入文字 -> 打字 / 按 Enter
  3. 熱鍵按鈕 (⌘C ⌘V ⌘A ...) 與自訂熱鍵
  4. 連續腳本: 一次輸入多個步驟依序執行

需要 macOS 授權「輔助使用 (Accessibility)」給執行此程式的 App/終端機
(系統設定 → 隱私權與安全性 → 輔助使用)。否則滑鼠鍵盤控制不會作用。

相依套件: pip3 install pyautogui   (macOS 上會一併裝 pyobjc)
執行:     python3 mac_tool.py
"""
import queue
import threading
import time
import tkinter as tk

try:
    import pyautogui
    pyautogui.FAILSAFE = True   # 滑鼠移到左上角(0,0)可緊急中止
    _OK, _ERR = True, ""
except Exception as e:          # pragma: no cover
    _OK, _ERR = False, str(e)

# ⌘ 在 pyautogui 的鍵名; 也接受 cmd/win/⌘ 等別名
_KEY_ALIAS = {"cmd": "command", "⌘": "command", "win": "command",
              "opt": "option", "⌥": "option", "ctrl": "ctrl", "⌃": "ctrl",
              "shift": "shift", "⇧": "shift"}

SCRIPT_HELP = (
    "# 一行一步, 依序執行:\n"
    "move 200 300        # 移動滑鼠\n"
    "click 200 300       # 點擊 (省略座標=目前位置)\n"
    "dblclick 200 300    # 雙擊\n"
    "type Hello World    # 打字\n"
    "enter               # 按 Enter\n"
    "key tab             # 按單一鍵 (tab/esc/space...)\n"
    "hotkey command c    # 熱鍵 (可多鍵: hotkey command shift 4)\n"
    "wait 0.5            # 等待秒數\n"
)


class MacTool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mac 滑鼠/鍵盤 測試工具")
        self.geometry("640x720")
        self.minsize(640, 720)
        self.f = ("Helvetica", 13)
        self.fb = ("Helvetica", 12)      # 按鈕字級
        self._q = queue.Queue()
        self._build()
        self.after(100, self._drain)
        if not _OK:
            self._log(f"缺少 pyautogui: {_ERR}\n請先: pip3 install pyautogui")

    # ---------------- 介面 ----------------
    def _build(self):
        f, fb = self.f, self.fb
        M = 14                      # 區塊左右外距
        ipad = {"padx": 12, "pady": 8}   # 區塊內距

        # 滑鼠
        mouse = tk.LabelFrame(self, text=" 滑鼠 ", font=f, padx=6, pady=6)
        mouse.pack(fill="x", padx=M, pady=(12, 6))
        row = tk.Frame(mouse); row.pack(fill="x", **ipad)
        tk.Label(row, text="X:", font=f).pack(side="left")
        self.e_x = tk.Entry(row, width=7, font=f); self.e_x.pack(side="left", padx=(4, 14))
        tk.Label(row, text="Y:", font=f).pack(side="left")
        self.e_y = tk.Entry(row, width=7, font=f); self.e_y.pack(side="left", padx=(4, 14))
        tk.Button(row, text="讀取目前座標", font=fb, command=self.read_pos).pack(side="right")
        row2 = tk.Frame(mouse); row2.pack(fill="x", padx=12, pady=(0, 8))
        tk.Button(row2, text="移動滑鼠", width=10, font=fb, command=lambda: self._mouse("move")).pack(side="left", padx=(0, 8))
        tk.Button(row2, text="左鍵點擊", width=10, font=fb, command=lambda: self._mouse("click")).pack(side="left", padx=8)
        tk.Button(row2, text="雙擊", width=8, font=fb, command=lambda: self._mouse("dbl")).pack(side="left", padx=8)

        # 鍵盤
        kb = tk.LabelFrame(self, text=" 鍵盤 ", font=f, padx=6, pady=6)
        kb.pack(fill="x", padx=M, pady=6)
        row3 = tk.Frame(kb); row3.pack(fill="x", **ipad)
        tk.Label(row3, text="文字:", font=f).pack(side="left")
        self.e_text = tk.Entry(row3, font=f); self.e_text.pack(side="left", fill="x", expand=True, padx=(6, 0))
        row4 = tk.Frame(kb); row4.pack(fill="x", padx=12, pady=(0, 8))
        tk.Button(row4, text="輸入文字", width=10, font=fb, command=self.type_text).pack(side="left", padx=(0, 8))
        tk.Button(row4, text="按 Enter", width=10, font=fb, command=lambda: self.key("enter")).pack(side="left", padx=8)
        tk.Button(row4, text="輸入文字 + Enter", width=16, font=fb, command=self.type_and_enter).pack(side="left", padx=8)

        # 熱鍵
        hk = tk.LabelFrame(self, text=" 熱鍵 (⌘ = Command) ", font=f, padx=6, pady=6)
        hk.pack(fill="x", padx=M, pady=6)
        row5 = tk.Frame(hk); row5.pack(fill="x", **ipad)
        for label, keys in (("⌘C 複製", ("command", "c")), ("⌘V 貼上", ("command", "v")),
                            ("⌘X 剪下", ("command", "x")), ("⌘A 全選", ("command", "a")),
                            ("⌘Z 復原", ("command", "z")), ("⌘S 儲存", ("command", "s"))):
            tk.Button(row5, text=label, width=8, font=fb,
                      command=lambda k=keys: self.hotkey(k)).pack(side="left", padx=3)
        row6 = tk.Frame(hk); row6.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(row6, text="自訂:", font=f).pack(side="left")
        self.e_hotkey = tk.Entry(row6, font=f); self.e_hotkey.pack(side="left", fill="x", expand=True, padx=(6, 8))
        self.e_hotkey.insert(0, "command shift 4")
        tk.Button(row6, text="送出熱鍵", font=fb,
                  command=lambda: self.hotkey(self.e_hotkey.get().split())).pack(side="left")

        # 連續腳本
        sc = tk.LabelFrame(self, text=" 連續腳本 (一行一步) ", font=f, padx=6, pady=6)
        sc.pack(fill="both", expand=True, padx=M, pady=6)
        self.script = tk.Text(sc, height=7, font=("Menlo", 12), wrap="none")
        self.script.pack(fill="both", expand=True, padx=6, pady=(6, 4))
        self.script.insert("1.0", SCRIPT_HELP)
        scb = tk.Frame(sc); scb.pack(fill="x", padx=6, pady=(0, 4))
        tk.Button(scb, text="執行腳本", width=10, font=fb, command=self.run_script).pack(side="left")
        tk.Button(scb, text="清空", width=8, font=fb,
                  command=lambda: self.script.delete("1.0", "end")).pack(side="left", padx=8)

        # Log
        logf = tk.LabelFrame(self, text=" 訊息 ", font=f, padx=6, pady=6)
        logf.pack(fill="both", expand=False, padx=M, pady=(6, 12))
        self.log = tk.Text(logf, height=5, font=("Menlo", 12))
        self.log.pack(fill="both", expand=True)

    # ---------------- log (跨執行緒安全) ----------------
    def _log(self, msg):
        self.log.insert("end", msg + "\n"); self.log.see("end")

    def _post(self, msg):
        self._q.put(msg)

    def _drain(self):
        try:
            while True:
                self._log(self._q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _xy(self):
        try:
            return int(self.e_x.get()), int(self.e_y.get())
        except ValueError:
            self._log("X/Y 請輸入整數")
            return None

    # ---------------- 直接動作 (主執行緒) ----------------
    def read_pos(self):
        if not _OK:
            return
        x, y = pyautogui.position()
        self.e_x.delete(0, "end"); self.e_x.insert(0, str(x))
        self.e_y.delete(0, "end"); self.e_y.insert(0, str(y))
        self._log(f"目前滑鼠座標: ({x}, {y})")

    def _mouse(self, kind):
        if not _OK:
            return
        xy = self._xy()
        if not xy:
            return
        if kind == "move":
            pyautogui.moveTo(xy[0], xy[1], duration=0.2); self._log(f"移動滑鼠 {xy}")
        elif kind == "click":
            pyautogui.click(xy[0], xy[1]); self._log(f"點擊 {xy}")
        elif kind == "dbl":
            pyautogui.doubleClick(xy[0], xy[1]); self._log(f"雙擊 {xy}")

    def type_text(self):
        if not _OK:
            return
        t = self.e_text.get(); pyautogui.write(t, interval=0.03); self._log(f"輸入: {t!r}")

    def key(self, name):
        if not _OK:
            return
        pyautogui.press(name); self._log(f"按鍵: {name}")

    def type_and_enter(self):
        if not _OK:
            return
        t = self.e_text.get(); pyautogui.write(t, interval=0.03); pyautogui.press("enter")
        self._log(f"輸入 + Enter: {t!r}")

    def hotkey(self, keys):
        if not _OK:
            return
        keys = [_KEY_ALIAS.get(k.lower(), k.lower()) for k in keys if k.strip()]
        if not keys:
            return
        pyautogui.hotkey(*keys); self._log(f"熱鍵: {'+'.join(keys)}")

    # ---------------- 連續腳本 (背景執行緒) ----------------
    def run_script(self):
        if not _OK:
            return
        lines = self.script.get("1.0", "end").splitlines()
        threading.Thread(target=self._run_script, args=(lines,), daemon=True).start()

    def _run_script(self, lines):
        self._post("=== 開始執行腳本 ===")
        for raw in lines:
            line = raw.split("#", 1)[0].strip()   # 去掉註解
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]
            try:
                if cmd == "move" and len(args) >= 2:
                    pyautogui.moveTo(int(args[0]), int(args[1]), duration=0.15)
                elif cmd == "click":
                    if len(args) >= 2:
                        pyautogui.click(int(args[0]), int(args[1]))
                    else:
                        pyautogui.click()
                elif cmd == "dblclick" and len(args) >= 2:
                    pyautogui.doubleClick(int(args[0]), int(args[1]))
                elif cmd == "type":
                    pyautogui.write(line[len("type"):].strip(), interval=0.03)
                elif cmd == "enter":
                    pyautogui.press("enter")
                elif cmd == "key" and args:
                    pyautogui.press(args[0])
                elif cmd == "hotkey" and args:
                    keys = [_KEY_ALIAS.get(k.lower(), k.lower()) for k in args]
                    pyautogui.hotkey(*keys)
                elif cmd == "wait" and args:
                    time.sleep(float(args[0]))
                else:
                    self._post(f"[略過] 無法解析: {line}")
                    continue
                self._post(f"[OK] {line}")
            except Exception as e:
                self._post(f"[錯誤] {line} -> {e}")
        self._post("=== 腳本結束 ===")


if __name__ == "__main__":
    MacTool().mainloop()
