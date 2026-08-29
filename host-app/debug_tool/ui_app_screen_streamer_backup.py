# -*- coding: utf-8 -*-
"""
仿造 UI_sample.png 的介面程式 (Tkinter)

- 視窗大小依照 UI_sample.png (該圖為 2x Retina 截圖 756x1184，
  邏輯尺寸為 378x592，故視窗採用邏輯尺寸)。
- 「配置」按鈕改成「測試」按鈕，按下後：
    1. 擷取螢幕，比對 target.png
    2. 找到後將中心座標填入「SN輸入框 X/Y 座標」欄位
    3. 將訊息輸出到上方的 Log 欄位
- 其餘按鈕僅為外觀，尚未實作功能。
"""

import os
import sys
import time
import socket
import threading
import queue

# ⚠️ DPI 感知必須在 import mss / pyautogui / tkinter 「之前」設定!
# 因為 mss / pyautogui 在 import 時會搶先把行程設成「系統級」DPI 並鎖定,
# 之後再設 Per-Monitor V2 會失敗 -> 不同縮放的副螢幕被虛擬化、座標與截圖
# 比例全部跑掉。所以這段要放在最前面。
# Per-Monitor Aware V2 讓整個桌面以實體像素呈現, 多螢幕不同縮放也一致。
try:
    import ctypes
    try:
        # PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()       # 系統級 (最舊)
except Exception:
    pass

import tkinter as tk
from tkinter import ttk

# 影像比對相關套件 (延遲匯入，缺少時於 Log 提示)
try:
    import cv2
    import numpy as np
    import pyautogui
    import mss
    _VISION_OK = True
    _VISION_ERR = ""
except Exception as e:  # pragma: no cover - 環境缺套件時
    _VISION_OK = False
    _VISION_ERR = str(e)


# ---- UI_sample.png 為 2x 截圖, 邏輯視窗尺寸 = 圖片尺寸 / 2 ----
WIN_W, WIN_H = 800, 1180

# ---- 操作延遲 (秒) ----
# 透過 JetKVM 控制遠端電腦時, 滑鼠/鍵盤/畫面都有來回延遲。
# 若打字會掉字或點擊沒反應, 把這幾個值「調大」。
CLICK_DELAY = 0.1     # 每次點擊後等待 (讓遠端處理完焦點/選取)
TYPE_INTERVAL = 0.05  # 每個字元之間的間隔 (KVM 逐鍵轉送, 太快會掉字)
AFTER_TYPE_DELAY = 0.1  # 打完字到按 Enter 之間
BEFORE_OK_DELAY = 0.1   # 按 Enter 後到點 OK 按鈕之間
KEY_HOLD = 0.03       # 每個按鍵「按下→放開」之間的按住時間。
#   太短時, KVM 的 USB HID 模擬會吞掉「放開」訊號 -> 遠端以為鍵一直被壓著
#   (例如 Enter 卡住狂跳)。若仍會卡鍵, 把這個值調大 (例如 0.05~0.08)。


def get_resource_path(relative_path):
    """ 取得 PyInstaller 打包後的正確檔案路徑 """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# ======================================================================
# 低階鍵盤輸入 (scancode 掃描碼)
# pyautogui 送鍵時 scancode=0, 透過 JetKVM 等「靠瀏覽器抓鍵盤」的環境,
# 瀏覽器 event.code 取不到 -> KVM 不轉送 -> 遠端收不到。
# 改用 SendInput + KEYEVENTF_SCANCODE, 模擬成真實硬體鍵盤, KVM 才收得到。
# ======================================================================
_SCANCODE_OK = sys.platform.startswith("win")
if _SCANCODE_OK:
    import ctypes as _ct

    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_SCANCODE = 0x0008
    _SHIFT_SCAN = 0x2A   # 左 Shift
    _SCAN_ENTER = 0x1C

    class _KBD(_ct.Structure):
        _fields_ = [("wVk", _ct.c_ushort), ("wScan", _ct.c_ushort),
                    ("dwFlags", _ct.c_ulong), ("time", _ct.c_ulong),
                    ("dwExtraInfo", _ct.POINTER(_ct.c_ulong))]

    class _MOUSE(_ct.Structure):   # 只為了讓聯集大小正確
        _fields_ = [("dx", _ct.c_long), ("dy", _ct.c_long),
                    ("mouseData", _ct.c_ulong), ("dwFlags", _ct.c_ulong),
                    ("time", _ct.c_ulong), ("dwExtraInfo", _ct.POINTER(_ct.c_ulong))]

    class _II(_ct.Union):
        _fields_ = [("ki", _KBD), ("mi", _MOUSE)]

    class _INPUT(_ct.Structure):
        _fields_ = [("type", _ct.c_ulong), ("ii", _II)]

    def _send_scan(scan, keyup):
        flags = _KEYEVENTF_SCANCODE | (_KEYEVENTF_KEYUP if keyup else 0)
        inp = _INPUT(type=1, ii=_II(ki=_KBD(0, scan, flags, 0, None)))
        _ct.windll.user32.SendInput(1, _ct.byref(inp), _ct.sizeof(inp))


def _tap_scan(scan, hold=None):
    """送一個完整按鍵: 按下 -> 按住 hold 秒 -> 放開。
    按住時間讓 KVM 確實收到「放開」, 避免卡鍵。"""
    if hold is None:
        hold = KEY_HOLD
    _send_scan(scan, False)
    time.sleep(hold)
    _send_scan(scan, True)
    time.sleep(0.01)


def kvm_type(text, interval=0.05):
    """逐字以掃描碼送出 (含自動 Shift), 讓 KVM/瀏覽器擷取得到。
    非 Windows 或失敗時退回 pyautogui。"""
    if not _SCANCODE_OK:
        pyautogui.write(text, interval=interval)
        return
    u = _ct.windll.user32
    for ch in text:
        res = u.VkKeyScanW(ord(ch))     # 取得 VK + Shift 狀態
        if res == -1:
            continue
        vk = res & 0xFF
        shift = (res >> 8) & 1
        scan = u.MapVirtualKeyW(vk, 0)  # VK -> 掃描碼
        if scan == 0:
            continue
        if shift:
            _send_scan(_SHIFT_SCAN, False)
            time.sleep(KEY_HOLD)
        _tap_scan(scan)
        if shift:
            _send_scan(_SHIFT_SCAN, True)
            time.sleep(0.01)
        time.sleep(interval)
    # 保險: 確保 Shift 一定放開 (避免卡住修飾鍵)
    _send_scan(_SHIFT_SCAN, True)


def kvm_press_enter():
    """以掃描碼送出 Enter (含按住時間 + 補送放開, 避免卡鍵)。"""
    if not _SCANCODE_OK:
        pyautogui.press("enter")
        return
    _tap_scan(_SCAN_ENTER)
    time.sleep(0.02)
    _send_scan(_SCAN_ENTER, True)   # 保險: 再補一次「放開」, 確保不卡住


def kvm_click(cx, cy, hold=None):
    """移到 (cx,cy) 後做一次完整左鍵點擊: 按下 -> 按住 -> 放開 ->(保險)再放開。
    透過 KVM 時, 按下與放開若太快, 放開訊號會被吞掉 -> 遠端以為左鍵一直壓著
    (移動滑鼠變成拖曳)。加按住時間並補送放開可避免。"""
    if hold is None:
        hold = KEY_HOLD
    pyautogui.moveTo(cx, cy)
    time.sleep(0.03)
    pyautogui.mouseDown(button="left")
    time.sleep(hold)
    pyautogui.mouseUp(button="left")
    time.sleep(0.02)
    pyautogui.mouseUp(button="left")   # 保險: 再放開一次, 確保不卡住


class AtlasUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Atlas2-403-V1.0.7")

        # 統一字型 (依作業系統挑選中文字體; 放大以符合較大的視窗)
        if sys.platform == "darwin":          # macOS
            fam, mono = "PingFang TC", "Menlo"
        elif sys.platform.startswith("win"):  # Windows
            fam, mono = "Microsoft JhengHei", "Consolas"
        else:                                  # Linux 等
            fam, mono = "Noto Sans CJK TC", "DejaVu Sans Mono"
        self.f_label = (fam, 11)
        self.f_entry = (fam, 11)
        self.f_btn = (fam, 11)
        self.f_mono = (mono, 11)

        # TCP 伺服器相關狀態
        self._srv_sock = None
        self._srv_thread = None
        self._srv_running = threading.Event()   # 設定=執行中, 清除=要停止
        self._log_queue = queue.Queue()         # 工作執行緒 -> GUI 的訊息佇列

        self._build_widgets()

        # 視窗高度依內容自動調整 (避免字體放大後底部按鈕被裁切),
        # 寬度維持 WIN_W。
        self.update_idletasks()
        req_h = self.winfo_reqheight()
        self.geometry(f"{WIN_W}x{req_h}")
        self.resizable(False, False)

        # 定期把背景執行緒的訊息搬到 Log (Tk 只能在主執行緒更新)
        self.after(100, self._drain_log_queue)
        # 關閉視窗時確實停掉伺服器
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # 介面建立 (改用 pack 自動排版, 元件不會互相覆蓋)
    # ------------------------------------------------------------------
    def _build_widgets(self):
        PADX = 12          # 左右邊距
        GAP = 8            # 區塊之間的垂直間距

        # ===== Log 區 =====
        log_frame = tk.LabelFrame(self, text="Log", font=self.f_label)
        log_frame.pack(fill="x", padx=PADX, pady=(GAP, GAP))

        btn_row = tk.Frame(log_frame)
        btn_row.pack(fill="x", padx=6, pady=6)
        tk.Button(btn_row, text="主機信息", font=self.f_btn).pack(side="left", padx=2)
        tk.Button(btn_row, text="外掛日誌", font=self.f_btn).pack(side="left", padx=2)
        tk.Button(btn_row, text="測試軟件日誌", font=self.f_btn).pack(side="left", padx=2)
        self.var_reconnect = tk.IntVar()
        tk.Checkbutton(btn_row, text="重連軟件", font=self.f_label,
                       variable=self.var_reconnect).pack(side="left", padx=6)

        # log 文字框 + 縱向/橫向捲軸 (用 grid 排版)
        txt_wrap = tk.Frame(log_frame)
        txt_wrap.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        vscroll = tk.Scrollbar(txt_wrap, orient="vertical")
        hscroll = tk.Scrollbar(txt_wrap, orient="horizontal")
        self.log_text = tk.Text(txt_wrap, height=8, font=self.f_mono,
                                fg="red", wrap="none",
                                yscrollcommand=vscroll.set,
                                xscrollcommand=hscroll.set)
        vscroll.config(command=self.log_text.yview)
        hscroll.config(command=self.log_text.xview)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")
        txt_wrap.rowconfigure(0, weight=1)
        txt_wrap.columnconfigure(0, weight=1)

        # 預設文字 (仿截圖)
        self._append_log("電腦IP:IP:127.0.0.1")
        self._append_log("**********")

        # ===== 串口 COM =====
        com_frame = tk.Frame(self)
        com_frame.pack(fill="x", padx=PADX, pady=GAP)
        tk.Label(com_frame, text="串口 COM:", font=self.f_label).pack(side="left")
        self.com_combo = ttk.Combobox(com_frame, font=self.f_entry, width=24,
                                      values=["/dev/cu.Bluetooth-Incoming-P..."])
        self.com_combo.current(0)
        self.com_combo.pack(side="left", padx=6)
        tk.Button(com_frame, text="關閉串口", font=self.f_btn).pack(side="right")

        # ===== 服務端 =====
        srv = tk.LabelFrame(self, text="服務端", font=self.f_label)
        srv.pack(fill="x", padx=PADX, pady=GAP)

        tk.Label(srv, text="測試通道端口信息:", font=self.f_label).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        # 啟動後此欄會顯示 IP:Port
        self.e_test_port = tk.Entry(srv, font=self.f_entry, width=20, justify="center")
        self.e_test_port.insert(0, "8888")
        self.e_test_port.grid(row=0, column=1, padx=8)
        self.btn_close_srv = tk.Button(srv, text="關閉服務器", font=self.f_btn,
                                       command=self.close_server, state="disabled")
        self.btn_close_srv.grid(row=0, column=2, padx=8)

        tk.Label(srv, text="心跳通道端口信息:", font=self.f_label).grid(row=1, column=0, sticky="w", padx=8, pady=8)
        self.e_hb_port = tk.Entry(srv, font=self.f_entry, width=10, justify="center")
        self.e_hb_port.insert(0, "7777")
        self.e_hb_port.grid(row=1, column=1, padx=8)
        self.btn_open_srv = tk.Button(srv, text="打開服務器", font=self.f_btn,
                                      command=self.open_server)
        self.btn_open_srv.grid(row=1, column=2, padx=8)

        # ===== 座標信息 =====
        coord = tk.LabelFrame(self, text="座標信息", font=self.f_label)
        coord.pack(fill="x", padx=PADX, pady=GAP)

        tk.Label(coord, text="SN輸入框X座標", font=self.f_label).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.e_sn_x = tk.Entry(coord, font=self.f_entry, width=7, justify="center")
        self.e_sn_x.insert(0, "70")
        self.e_sn_x.grid(row=0, column=1, padx=8)
        tk.Label(coord, text="Start按鈕X座標", font=self.f_label).grid(row=0, column=2, sticky="w", padx=(24, 8))
        self.e_start_x = tk.Entry(coord, font=self.f_entry, width=7, justify="center")
        self.e_start_x.insert(0, "850")
        self.e_start_x.grid(row=0, column=3, padx=8)

        tk.Label(coord, text="SN輸入框Y座標", font=self.f_label).grid(row=1, column=0, sticky="w", padx=8, pady=8)
        self.e_sn_y = tk.Entry(coord, font=self.f_entry, width=7, justify="center")
        self.e_sn_y.insert(0, "260")
        self.e_sn_y.grid(row=1, column=1, padx=8)
        tk.Label(coord, text="Start按鈕Y座標", font=self.f_label).grid(row=1, column=2, sticky="w", padx=(24, 8))
        self.e_start_y = tk.Entry(coord, font=self.f_entry, width=7, justify="center")
        self.e_start_y.insert(0, "264")
        self.e_start_y.grid(row=1, column=3, padx=8)

        # 螢幕選擇: 決定「測試」時要擷取哪一個螢幕 (只抓單一螢幕可加速)
        tk.Label(coord, text="擷取螢幕", font=self.f_label).grid(row=2, column=0, sticky="w", padx=8, pady=8)
        self.screen_combo = ttk.Combobox(coord, font=self.f_entry, width=26,
                                         state="readonly", postcommand=self._refresh_screens)
        self.screen_combo.grid(row=2, column=1, columnspan=3, sticky="w", padx=8)
        self._populate_screens()

        # ===== 路徑欄位 =====
        def add_path(label_text, default):
            tk.Label(self, text=label_text, font=self.f_label).pack(
                anchor="w", padx=PADX, pady=(GAP, 0))
            entry = tk.Entry(self, font=self.f_entry)
            entry.insert(0, default)
            entry.pack(fill="x", padx=PADX, ipady=3)
            return entry

        self.e_log_path = add_path("Log 日誌路徑：", "/Users/gdlocal/Library/Logs/Atlas/active")
        self.e_csv_path = add_path("CSV 文件路徑：", "/Users/gdlocal/Library/Logs/Atlas/unit-archive")
        self.e_sw_path = add_path("測試軟件路徑：",
                                  "/Users/mac/Documents/WiPASXNext_0_1/test_station_out_wipas")

        # ===== 底部按鈕 =====
        btm = tk.Frame(self)
        btm.pack(fill="x", side="bottom", padx=PADX, pady=GAP * 2)
        for i in range(3):
            btm.columnconfigure(i, weight=1)
        tk.Button(btm, text="登陸", font=self.f_btn, width=10).grid(row=0, column=0)
        tk.Button(btm, text="Switch", font=self.f_btn, width=10).grid(row=0, column=1)
        # 「配置」改成「測試」, 綁定影像比對功能
        tk.Button(btm, text="測試", font=self.f_btn, width=10,
                  command=self.on_test).grid(row=0, column=2)

    def _populate_screens(self):
        """讀取目前所有螢幕, 填入下拉選單。
        選項順序對應 mss.monitors 索引: 0=全部(虛擬桌面), 1..N=各別螢幕。"""
        labels = ["全部螢幕 (虛擬桌面)"]
        if _VISION_OK:
            try:
                with mss.mss() as sct:
                    mons = sct.monitors
                for i in range(1, len(mons)):
                    m = mons[i]
                    pos = "主" if (m["left"] == 0 and m["top"] == 0) else "副"
                    labels.append(f"螢幕 {i} [{pos}] ({m['width']}x{m['height']})")
            except Exception:
                pass
        self.screen_combo["values"] = labels
        self.screen_combo.current(0)

    def _refresh_screens(self):
        """下拉選單打開時即時重抓螢幕清單 (避免顯示過時的螢幕資訊),
        並盡量保留原本選擇的項目。"""
        cur = self.screen_combo.current()
        self._populate_screens()
        values = self.screen_combo["values"]
        if 0 <= cur < len(values):
            self.screen_combo.current(cur)

    # ------------------------------------------------------------------
    # 功能: 測試按鈕
    # ------------------------------------------------------------------
    def _append_log(self, msg):
        ts = time.strftime("%Y-%m-%d(%H:%M:%S)")
        self.log_text.insert("end", f"{ts}: {msg}\n")
        self.log_text.see("end")
        self.update_idletasks()

    # ------------------------------------------------------------------
    # TCP/IP 伺服器 (背景執行緒 + 佇列回報 Log)
    # ------------------------------------------------------------------
    def _post_log(self, msg):
        """供背景執行緒使用: 把訊息丟進佇列, 由主執行緒寫到 Log。"""
        self._log_queue.put(msg)

    def _drain_log_queue(self):
        """主執行緒定期執行: 把佇列裡的訊息寫進 Log。"""
        try:
            while True:
                self._append_log(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _get_local_ip(self):
        """取得本機對外的 IP (失敗則回 127.0.0.1)。不會真的送出封包。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _parse_port(self, text, default=8888):
        """從欄位文字解析出 Port (可接受 '8888' 或 'ip:8888')。"""
        text = (text or "").strip()
        if ":" in text:
            text = text.rsplit(":", 1)[-1]
        digits = "".join(ch for ch in text if ch.isdigit())
        try:
            port = int(digits)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
        return default

    def open_server(self):
        """按下「打開服務器」: 啟動 TCP 伺服器, 背景監聽字串訊息。"""
        if self._srv_running.is_set():
            self._append_log("服務器已在執行中")
            return

        port = self._parse_port(self.e_test_port.get())
        host = "0.0.0.0"  # 監聽所有介面 (本機程式用 127.0.0.1 即可連到)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(5)
            sock.settimeout(0.5)  # 每 500ms 醒來一次, 以便檢查停止旗標
        except OSError as e:
            self._append_log(f"服務器啟動失敗 (Port {port}): {e}")
            return

        self._srv_sock = sock
        self._srv_running.set()

        # 測試通道端口欄位顯示 IP:Port
        ip = self._get_local_ip()
        self.e_test_port.delete(0, "end")
        self.e_test_port.insert(0, f"{ip}:{port}")

        self._srv_thread = threading.Thread(target=self._server_loop, daemon=True)
        self._srv_thread.start()

        self.btn_open_srv.config(state="disabled")
        self.btn_close_srv.config(state="normal")
        self._append_log(f"服務器已啟動，監聽 {ip}:{port} (本機可用 127.0.0.1:{port})")

    def close_server(self):
        """按下「關閉服務器」: 停止監聽。"""
        if not self._srv_running.is_set():
            return
        self._srv_running.clear()   # 通知執行緒停止
        try:
            if self._srv_sock:
                self._srv_sock.close()
        except Exception:
            pass
        self._srv_sock = None
        self.btn_open_srv.config(state="normal")
        self.btn_close_srv.config(state="disabled")
        self._append_log("服務器已關閉")

    def _server_loop(self):
        """背景執行緒: 接受連線, 每 500ms 檢查一次停止旗標。"""
        sock = self._srv_sock
        while self._srv_running.is_set():
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue  # 500ms 沒連線, 回頭檢查是否該停止
            except OSError:
                break     # socket 已被關閉
            # 每個連線開一個執行緒處理, 不擋住其他連線
            threading.Thread(target=self._handle_conn, args=(conn, addr),
                             daemon=True).start()

    def _handle_conn(self, conn, addr):
        """處理單一連線: 收到字串 -> Log -> 回傳 'OK'。"""
        peer = f"{addr[0]}:{addr[1]}"
        self._post_log(f"連線建立: {peer}")
        with conn:
            conn.settimeout(0.5)
            while self._srv_running.is_set():
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue  # 沒資料, 每 500ms 檢查停止旗標
                except OSError:
                    break
                if not data:
                    break     # 對方關閉連線
                msg = data.decode("utf-8", errors="replace").strip()
                self._post_log(f"收到訊息 [{peer}]: {msg}")
                try:
                    conn.sendall(b"OK\r\n")   # 回傳 OK
                except OSError:
                    break
        self._post_log(f"連線結束: {peer}")

    def _on_close(self):
        """關閉視窗: 先停掉伺服器再離開。"""
        try:
            self.close_server()
        except Exception:
            pass
        self.destroy()

    def _load_gray(self, rel_path):
        """讀取 rel_path 圖片並轉灰階。回傳 (灰階影像 或 None, 完整路徑)。"""
        path = get_resource_path(rel_path)
        img = cv2.imread(path)
        if img is None:
            return None, path
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), path

    def _locate(self, gray, tmpl_gray, downscale=True):
        """在灰階影像 gray 中比對範本 tmpl_gray (粗搜尋 + 全解析度精修)。
        回傳 (最高相似度, left, top, w, h)；找不到時 left 等為 None。
        座標皆為 gray 影像內的整數像素。

        downscale=True : 先縮小 0.5 倍做粗搜尋, 適合在「整個螢幕」找大目標(視窗)。
        downscale=False: 直接全解析度比對, 適合在小範圍(視窗內)找「小元件」
                         (例如 OK 按鈕), 避免縮小後細節糊掉而比對到錯位置。"""
        H, W = gray.shape[:2]
        th, tw = tmpl_gray.shape[:2]
        DS = 0.5 if downscale else 1.0
        small = (cv2.resize(gray, None, fx=DS, fy=DS, interpolation=cv2.INTER_AREA)
                 if DS != 1.0 else gray)

        # ---- 1. 粗搜尋 (縮小圖, 多尺度) ----
        coarse_scales = [0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]
        c_val, c_loc, c_scale = -1.0, None, None
        for s in coarse_scales:
            nw, nh = int(tw * s * DS), int(th * s * DS)
            if nw < 8 or nh < 8 or nw > small.shape[1] or nh > small.shape[0]:
                continue
            t = cv2.resize(tmpl_gray, (nw, nh), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(small, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, mloc = cv2.minMaxLoc(res)
            if mx > c_val:
                c_val, c_loc, c_scale = mx, mloc, s
        if c_loc is None:
            return -1.0, None, None, None, None

        fx, fy = c_loc[0] / DS, c_loc[1] / DS

        # ---- 2. 全解析度精修 (只在候選區域) ----
        margin = int(max(tw, th) * c_scale * 0.25) + 10
        x0 = max(0, int(fx - margin))
        y0 = max(0, int(fy - margin))
        x1 = min(W, int(fx + tw * c_scale + margin))
        y1 = min(H, int(fy + th * c_scale + margin))
        roi = gray[y0:y1, x0:x1]

        best = (c_val, None, None, None, None)
        for k in (0.85, 0.92, 1.0, 1.08, 1.15):
            rs = c_scale * k
            nw, nh = int(tw * rs), int(th * rs)
            if nw < 8 or nh < 8 or nw > roi.shape[1] or nh > roi.shape[0]:
                continue
            t = cv2.resize(tmpl_gray, (nw, nh), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, mloc = cv2.minMaxLoc(res)
            if mx > best[0]:
                best = (mx, x0 + mloc[0], y0 + mloc[1], nw, nh)

        if best[1] is None:
            # 精修沒結果, 退回粗搜尋的方框
            return c_val, int(fx), int(fy), int(tw * c_scale), int(th * c_scale)
        return best

    def on_test(self):
        """按下測試:
        1. 找到 target.png 視窗, 在視窗內找 Input_target.png 輸入框 -> 點擊輸入 SN_ABC
        2. 在視窗內找 Button_target.png 按鈕 -> 座標填入 Start 欄位 -> 左鍵點擊
        """
        if not _VISION_OK:
            self._append_log(f"缺少影像套件 (cv2/numpy/pyautogui): {_VISION_ERR}")
            return

        # 三張範本都放在 FCT 資料夾
        win_g, win_p = self._load_gray(os.path.join("FCT", "target.png"))
        inp_g, inp_p = self._load_gray(os.path.join("FCT", "Input_target.png"))
        btn_g, btn_p = self._load_gray(os.path.join("FCT", "Button_target.png"))
        for g, p in ((win_g, win_p), (inp_g, inp_p), (btn_g, btn_p)):
            if g is None:
                self._append_log(f"讀取圖片失敗: {p}")
                return

        threshold = 0.6
        t0 = time.perf_counter()

        # ---- 步驟 1: 在選定的螢幕中找出 target.png 視窗 ----
        mon_index = self.screen_combo.current()
        if mon_index < 0:
            mon_index = 0

        best = None
        with mss.mss() as sct:
            n = len(sct.monitors)
            if mon_index == 0:
                targets = range(1, n)
            elif mon_index < n:
                targets = [mon_index]
            else:
                # 下拉選單的選擇超出目前螢幕數(清單過時), 退回掃描全部
                self._append_log("螢幕清單已變動, 自動改掃全部螢幕")
                targets = range(1, n)
            for mi in targets:
                m = sct.monitors[mi]
                gray = cv2.cvtColor(np.array(sct.grab(m)), cv2.COLOR_BGRA2GRAY)
                val, l, t, w, h = self._locate(gray, win_g)
                # 印出每個螢幕的相似度, 方便看出視窗在哪個螢幕
                self._append_log(
                    f"  螢幕 {mi} ({m['width']}x{m['height']}): 視窗相似度 {val:.2f}"
                )
                if best is None or val > best["val"]:
                    best = {"val": val, "l": l, "t": t, "w": w, "h": h,
                            "mon": m, "mi": mi, "gray": gray}

        if best is None or best["val"] < threshold or best["l"] is None:
            v = best["val"] if best else -1
            self._append_log(f"未找到目標視窗 target.png (最高相似度 {v:.2f} < {threshold})")
            return

        m, gray = best["mon"], best["gray"]
        px0, py0, pw, ph = best["l"], best["t"], best["w"], best["h"]
        self._append_log(
            f"找到視窗 (螢幕 {best['mi']}，相似度 {best['val']:.2f})，"
            f"範圍 {pw}x{ph}"
        )

        # 視窗範圍內的子影像 (後續只在這塊裡找, 避免畫面其他地方誤判)
        win_roi = gray[py0:py0 + ph, px0:px0 + pw]

        # 子影像在視窗內的座標 -> 螢幕絕對座標。
        # fx 為點擊的水平位置比例 (0=最左, 0.5=中心, 1=最右);
        # 輸入框因左側有標籤, 用偏右的位置避開標籤。
        def to_abs(left, top, w, h, fx=0.5):
            cx = int(m["left"] + px0 + left + w * fx)
            cy = int(m["top"] + py0 + top + h / 2)
            return cx, cy

        # ---- 步驟 1b: 在視窗內找輸入框, 點擊並輸入 SN_ABC ----
        iv, il, it, iw, ih = self._locate(win_roi, inp_g, downscale=False)
        if iv >= threshold and il is not None:
            cx, cy = to_abs(il, it, iw, ih, fx=0.75)  # 偏右, 避開 SN_input 標籤
            # 同步顯示在「SN輸入框」座標欄
            self.e_sn_x.delete(0, "end"); self.e_sn_x.insert(0, str(cx))
            self.e_sn_y.delete(0, "end"); self.e_sn_y.insert(0, str(cy))
            self._append_log(f"找到輸入框 (相似度 {iv:.2f})，座標 ({cx}, {cy})，輸入 SN_ABC")
            # 透過 KVM 控制遠端: 用 kvm_click 確保「放開」不會被吞掉。
            kvm_click(cx, cy)
            time.sleep(CLICK_DELAY)
            # 用掃描碼送鍵 (KVM 才收得到); 直接打字會覆蓋儲存格舊內容
            kvm_type("SN_ABC", interval=TYPE_INTERVAL)
            time.sleep(AFTER_TYPE_DELAY)
            # 不按 Enter, 直接靠後面點 OK 按鈕送出
        else:
            self._append_log(f"視窗內未找到輸入框 Input_target.png (相似度 {iv:.2f})")

        # ---- 步驟 2: 在視窗內找按鈕, 座標填入 Start 欄位並左鍵點擊 ----
        # OK 按鈕一定在輸入框「右側」, 故只搜尋輸入框右緣以右的區域,
        # 避免重複測試後遠端畫面殘留狀態, 讓按鈕誤判到輸入框附近。
        if il is not None:
            search_x0 = il + iw
        else:
            search_x0 = 0
        btn_region = win_roi[:, search_x0:]
        bv, bl, bt, bw, bh = self._locate(btn_region, btn_g, downscale=False)
        if bv >= threshold and bl is not None:
            cx, cy = to_abs(bl + search_x0, bt, bw, bh)
            self.e_start_x.delete(0, "end"); self.e_start_x.insert(0, str(cx))
            self.e_start_y.delete(0, "end"); self.e_start_y.insert(0, str(cy))
            self._append_log(f"找到按鈕 (相似度 {bv:.2f})，座標 ({cx}, {cy})，左鍵點擊")
            # 透過 KVM: 用 kvm_click 確保左鍵「放開」不被吞掉 (否則移動會變拖曳)
            time.sleep(CLICK_DELAY)
            kvm_click(cx, cy)
        else:
            self._append_log(f"視窗內未找到按鈕 Button_target.png (相似度 {bv:.2f})")

        self._append_log(f"完成，耗時 {time.perf_counter() - t0:.2f}s")


if __name__ == "__main__":
    app = AtlasUI()
    app.mainloop()
