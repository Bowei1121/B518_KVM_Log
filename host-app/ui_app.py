# -*- coding: utf-8 -*-
"""
- 按鍵精靈介面程式
"""

import os
import sys
import time
import socket
import threading
import queue

# DPI 策略: 行程用「系統級 (System Aware)」DPI 感知 -> 視窗跨不同縮放的延伸
# 螢幕時不會被重新調整大小 (穩定), 主螢幕也維持正常大小。
try:
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))  # SYSTEM_AWARE
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()       # 系統級 (最舊 API)
except Exception:
    pass

import tkinter as tk
from template_catalog import TemplateCatalog

# cv2 用於「擷取影像」存檔 (延遲匯入，缺少時於 Log 提示)
try:
    import cv2
    _VISION_OK = True
    _VISION_ERR = ""
except Exception as e:  # pragma: no cover - 環境缺套件時
    _VISION_OK = False
    _VISION_ERR = str(e)

# JetKVM 直接連線 (WebRTC); 缺套件時停用 KVM 連接功能
try:
    from jetkvm_core import JetKVMClient, AsyncLoop, grab_one_frame
    from pattern_tools import PatternCropper
    from auto_flow import run_flow, run_check
    from stream_view import StreamerWindow
    _KVM_OK = True
    _KVM_ERR = ""
except Exception as e:
    _KVM_OK = False
    _KVM_ERR = str(e)


# ---- 邏輯視窗尺寸 = 圖片尺寸 / 2 ----
WIN_W, WIN_H = 800, 1180


class AtlasUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Atlas2-518-260624-V1.0")

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
        self._ui_queue = queue.Queue()          # 背景執行緒要在主執行緒跑的動作

        # JetKVM 連線相關狀態
        self._kvm = None                        # JetKVMClient 實例
        self._kvm_loop = None                   # 背景 asyncio loop (延遲建立)
        self._kvm_busy = False                  # 連線/斷線進行中
        self._switch_busy = False               # Switch 流程進行中
        self._streamer = None                   # 串流視窗 (StreamerWindow)
        self._cmd_lock = threading.Lock()       # TCP 指令序列化處理
        self._templates = TemplateCatalog()

        self._build_widgets()

        # 視窗高度依內容自動調整 (避免字體放大後底部按鈕被裁切),
        # 寬度維持 WIN_W。
        self.update_idletasks()
        req_h = self.winfo_reqheight()
        self.geometry(f"{WIN_W}x{req_h}")
        self.resizable(False, False)

        # 背景預熱 OCR 模型 (避免第一次 check 指令時卡 1~2 秒載入)
        threading.Thread(target=self._warm_ocr, daemon=True).start()

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

        # ===== 服務端 =====
        srv = tk.LabelFrame(self, text="服務端", font=self.f_label)
        srv.pack(fill="x", padx=PADX, pady=GAP)

        # 第 0 列: 測試畫面 IP + KVM 連接
        tk.Label(srv, text="測試畫面IP:", font=self.f_label).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.e_kvm_ip = tk.Entry(srv, font=self.f_entry, width=20, justify="center")
        self.e_kvm_ip.insert(0, "192.168.132.70")
        self.e_kvm_ip.grid(row=0, column=1, padx=8)
        self.btn_kvm = tk.Button(srv, text="KVM連接", font=self.f_btn,
                                 command=self.toggle_kvm)
        self.btn_kvm.grid(row=0, column=2, padx=8)

        # 第 1 列: 監聽指令訊息 (IP:PORT) + 監聽連接
        tk.Label(srv, text="監聽指令IP:", font=self.f_label).grid(row=1, column=0, sticky="w", padx=8, pady=8)
        self.e_listen_addr = tk.Entry(srv, font=self.f_entry, width=20, justify="center")
        self.e_listen_addr.insert(0, "127.0.0.1:8888")
        self.e_listen_addr.grid(row=1, column=1, padx=8)
        self.btn_listen = tk.Button(srv, text="監聽連接", font=self.f_btn,
                                    command=self.toggle_listen)
        self.btn_listen.grid(row=1, column=2, padx=8)

        # 第 2 列: 顯示最近收到的指令 (設備種類 / 設備編號 / 收到指令)
        self.var_dev_type = tk.StringVar()
        self.var_dev_no = tk.StringVar()
        self.var_func = tk.StringVar()
        disp = tk.Frame(srv)
        disp.grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))
        for lbl, var, w in (("設備種類", self.var_dev_type, 6),
                            ("設備編號", self.var_dev_no, 4),
                            ("收到指令", self.var_func, 8)):
            tk.Label(disp, text=lbl + ":", font=self.f_label).pack(side="left")
            tk.Entry(disp, textvariable=var, state="readonly", width=w,
                     justify="center", font=self.f_entry).pack(side="left", padx=(2, 12))

        # ===== Pattern 區 =====
        pat = tk.LabelFrame(self, text="Pattern", font=self.f_label)
        pat.pack(fill="x", padx=PADX, pady=GAP)

        tk.Label(pat, text="模板根目錄（由設備與模板種類自動命名）：", font=self.f_label).pack(
            anchor="w", padx=8, pady=(6, 0))
        template_root = tk.Entry(pat, font=self.f_entry)
        template_root.insert(0, str(self._templates.root))
        template_root.config(state="readonly")
        template_root.pack(fill="x", padx=8, ipady=3)

        pat_btns = tk.Frame(pat)
        pat_btns.pack(fill="x", padx=8, pady=6)
        tk.Button(pat_btns, text="擷取影像", font=self.f_btn,
                  command=self.on_capture_image).pack(side="left", padx=(0, 6))
        tk.Button(pat_btns, text="創建Pattern", font=self.f_btn,
                  command=self.on_create_pattern).pack(side="left")

        # ===== 底部按鈕 =====
        btm = tk.Frame(self)
        btm.pack(fill="x", side="bottom", padx=PADX, pady=GAP * 2)
        for i in range(3):
            btm.columnconfigure(i, weight=1)
        tk.Button(btm, text="Streamer", font=self.f_btn, width=10,
                  command=self.on_streamer).grid(row=0, column=0)
        self.btn_switch = tk.Button(btm, text="Switch", font=self.f_btn, width=10,
                                    command=self.on_switch)
        self.btn_switch.grid(row=0, column=1)

    @staticmethod
    def _warm_ocr():
        """背景載入 OCR 模型 (縮短第一次 check 的等待)。"""
        try:
            import ocr_sn
            ocr_sn.warmup()
        except Exception:
            pass

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
        """主執行緒定期執行: 把佇列裡的訊息寫進 Log, 並執行排入的 UI 動作。"""
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
                except Exception as e:
                    self._append_log(f"UI 動作錯誤: {e}")
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _run_on_ui(self, fn):
        """背景執行緒呼叫: 排一個動作到主執行緒執行 (更新 Tk 元件用)。"""
        self._ui_queue.put(fn)

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

    def _local_ips(self):
        """列出本機所有 IPv4 位址 (供綁定監聽參考)。"""
        ips = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ips.add(info[4][0])
        except Exception:
            pass
        ips.add("127.0.0.1")
        return sorted(ips)

    def _parse_addr(self, text, default_host="0.0.0.0", default_port=8888):
        """解析 'ip:port' / ':port' / 'port' -> (host, port)。"""
        text = (text or "").strip()
        host, port = default_host, default_port
        if ":" in text:
            h, _, p = text.rpartition(":")
            host = h.strip() or default_host
            digits = "".join(ch for ch in p if ch.isdigit())
        else:
            digits = "".join(ch for ch in text if ch.isdigit())
        try:
            v = int(digits)
            if 1 <= v <= 65535:
                port = v
        except ValueError:
            pass
        return host, port

    # ------------------------------------------------------------------
    # 監聽連接 (TCP 伺服器)
    # ------------------------------------------------------------------
    def toggle_listen(self):
        """「監聽連接」<-> 「關閉監聽」。"""
        if self._srv_running.is_set():
            self._stop_listen()
        else:
            self._start_listen()

    def _start_listen(self):
        host, port = self._parse_addr(self.e_listen_addr.get())
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(5)
            sock.settimeout(0.5)  # 每 500ms 醒來一次, 以便檢查停止旗標
        except OSError as e:
            self._append_log(f"監聽啟動失敗 ({host}:{port}): {e}")
            # 10049 = 該 IP 不是本機網卡位址。提示正確用法與可用 IP。
            if getattr(e, "winerror", None) == 10049 or "10049" in str(e):
                ips = self._local_ips()
                self._append_log("  監聽 IP 必須是『本機』網卡位址或 0.0.0.0(代表所有網卡)")
                self._append_log(f"  本機可用 IP: 0.0.0.0(全部), {', '.join(ips)}")
            return
        self._srv_sock = sock
        self._srv_running.set()
        self._srv_thread = threading.Thread(target=self._server_loop, daemon=True)
        self._srv_thread.start()
        self.btn_listen.config(text="關閉監聽")
        self._append_log(f"開始監聽 {host}:{port} (本機可用 127.0.0.1:{port})")

    def _stop_listen(self):
        self._srv_running.clear()
        try:
            if self._srv_sock:
                self._srv_sock.close()
        except Exception:
            pass
        self._srv_sock = None
        self.btn_listen.config(text="監聽連接")
        self._append_log("已停止監聽")

    # ------------------------------------------------------------------
    # KVM 連接 (JetKVM WebRTC, 保持連線)
    # ------------------------------------------------------------------
    def toggle_kvm(self):
        """「KVM連接」<-> 「關閉連接」。"""
        if self._kvm_busy:
            return
        if self._kvm and self._kvm.connected:
            self._disconnect_kvm()
        else:
            self._connect_kvm()

    def _connect_kvm(self):
        if not _KVM_OK:
            self._append_log(f"無法使用 KVM 連線 (缺套件): {_KVM_ERR}")
            return
        ip = self.e_kvm_ip.get().strip()
        if not ip:
            self._append_log("請先在『測試畫面IP』填入 KVM IP")
            return
        if self._kvm_loop is None:
            self._kvm_loop = AsyncLoop()      # 背景 asyncio loop
        self._kvm = JetKVMClient(ip)
        self._kvm_busy = True
        self.btn_kvm.config(text="連線中...", state="disabled")
        self._append_log(f"正在連線 KVM {ip} ...")

        fut = self._kvm_loop.submit(self._kvm.connect())

        def done(f):
            self._kvm_busy = False
            try:
                f.result()
                w, h = self._kvm.size
                self._run_on_ui(lambda: self.btn_kvm.config(text="關閉連接", state="normal"))
                self._post_log(f"KVM 已連線, 遠端畫面 {w}x{h}")
            except Exception as e:
                err = str(e) or type(e).__name__
                self._run_on_ui(lambda: self.btn_kvm.config(text="KVM連接", state="normal"))
                self._post_log(f"KVM 連線失敗: {err}")

        fut.add_done_callback(done)

    def _disconnect_kvm(self):
        self._kvm_busy = True
        self.btn_kvm.config(text="斷線中...", state="disabled")
        fut = self._kvm_loop.submit(self._kvm.close())

        def done(f):
            self._kvm_busy = False
            self._run_on_ui(lambda: self.btn_kvm.config(text="KVM連接", state="normal"))
            self._post_log("KVM 已斷線")

        fut.add_done_callback(done)

    # ------------------------------------------------------------------
    # Pattern: 擷取影像 / 創建 Pattern
    # ------------------------------------------------------------------
    def on_capture_image(self):
        """擷取『測試畫面IP』的影像，存為模板根目錄下的原始擷取圖。
        已建立 KVM 連線就直接用最新影格; 否則一次性連線擷取。"""
        save_path = self._templates.capture_path()
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 已連線: 直接用保持中的最新影格
        if self._kvm and self._kvm.connected and self._kvm.frame is not None:
            try:
                cv2.imwrite(str(save_path), self._kvm.frame)
                self._append_log(f"已擷取影像 (使用現有連線) -> {save_path}")
            except Exception as e:
                self._append_log(f"擷取影像存檔失敗: {e}")
            return

        # 未連線: 一次性連線擷取
        if not _KVM_OK:
            self._append_log(f"無法擷取影像 (缺套件): {_KVM_ERR}")
            return
        ip = self.e_kvm_ip.get().strip()
        if not ip:
            self._append_log("請先在『測試畫面IP』填入 KVM IP")
            return
        if self._kvm_loop is None:
            self._kvm_loop = AsyncLoop()
        self._append_log(f"正在擷取 {ip} 的影像 (一次性連線) ...")
        fut = self._kvm_loop.submit(grab_one_frame(ip))

        def done(f):
            try:
                frame = f.result()
                if frame is None:
                    self._post_log("擷取失敗: 沒有影格 (確認遠端有畫面)")
                    return
                cv2.imwrite(str(save_path), frame)
                self._post_log(f"已擷取影像 -> {save_path}")
            except Exception as e:
                self._post_log(f"擷取影像失敗: {e or type(e).__name__}")

        fut.add_done_callback(done)

    def on_create_pattern(self):
        """開啟設備／模板種類選單，從最新擷取圖製作標準化模板。"""
        if not _KVM_OK:
            self._append_log(f"無法創建 Pattern (缺套件): {_KVM_ERR}")
            return
        src = self._templates.capture_path()
        if not src.exists():
            self._append_log(f"找不到影格 {src}，請先按『擷取影像』")
            return
        self._append_log("請在彈出視窗選擇設備／模板種類並框選 Pattern (Esc 取消)")
        PatternCropper(self, self._templates, str(src), device="FCT", template_key="window",
                       on_saved=self._append_log)

    # ------------------------------------------------------------------
    # Switch: 自動流程 (找視窗 -> 輸入 SN -> 點 OK), 用已連線的 KVM
    # ------------------------------------------------------------------
    def on_switch(self):
        if not _KVM_OK:
            self._append_log(f"無法執行 Switch (缺套件): {_KVM_ERR}")
            return
        if self._switch_busy:
            return
        if not (self._kvm and self._kvm.connected):
            self._append_log("請先按『KVM連接』建立連線, 再按 Switch")
            return

        annotate = self._templates.diagnostic_path("FCT", "jetkvm_detected.png")
        annotate.parent.mkdir(parents=True, exist_ok=True)
        self._switch_busy = True
        self.btn_switch.config(state="disabled")
        self._append_log("Switch: 開始自動流程 ...")

        fut = self._kvm_loop.submit(
            run_flow(self._kvm, "FCT", template_root=self._templates.root, sn_text="SN_ABC", threshold=0.8,
                     log=self._post_log, annotate_path=str(annotate)))

        def done(f):
            self._switch_busy = False
            self._run_on_ui(lambda: self.btn_switch.config(state="normal"))
            try:
                f.result()
            except Exception as e:
                self._post_log(f"Switch 流程錯誤: {e or type(e).__name__}")

        fut.add_done_callback(done)

    # ------------------------------------------------------------------
    # Streamer: 把 KVM 連線的遠端畫面即時投到視窗
    # ------------------------------------------------------------------
    def _kvm_send(self, method, params):
        """供串流視窗的互動操作呼叫: 把 JSON-RPC 指令排到 KVM 的事件迴圈執行緒送出。
        (aiortc 物件只能在自己的 loop 執行緒操作, 故用 call_soon_threadsafe。)"""
        if self._kvm and self._kvm.connected and self._kvm_loop:
            try:
                self._kvm_loop.loop.call_soon_threadsafe(self._kvm.call, method, params)
            except Exception:
                pass

    def on_streamer(self):
        if not _KVM_OK:
            self._append_log(f"無法開啟串流 (缺套件): {_KVM_ERR}")
            return
        # 已開著就帶到前景
        if self._streamer is not None:
            try:
                self._streamer.lift()
                return
            except Exception:
                self._streamer = None
        if not (self._kvm and self._kvm.connected):
            self._append_log("請先按『KVM連接』建立連線, 再按 Streamer")
            return

        def _on_close():
            self._streamer = None
            self._append_log("已關閉串流視窗")

        self._streamer = StreamerWindow(
            self, frame_getter=lambda: self._kvm.frame if self._kvm else None,
            input_sender=self._kvm_send, fps=30,
            title=f"Streamer - {self.e_kvm_ip.get().strip()}",
            on_close=_on_close)
        self._append_log("已開啟串流視窗 (可點畫面操作遠端滑鼠/鍵盤)")

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
        """處理單一連線: 收到指令 -> 執行 -> 回傳結果 (action_done / error...)。"""
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
                # 可能一次收到多行指令
                for line in data.decode("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # 執行 (序列化, 避免同時操作 KVM)
                    with self._cmd_lock:
                        reply = self._process_command(line)
                    # 動作完成才回覆 (action_done 或 error:...); reply 已含 \r\n
                    try:
                        conn.sendall(reply.encode("utf-8"))
                    except OSError:
                        break
        self._post_log(f"連線結束: {peer}")

    # ------------------------------------------------------------------
    # TCP 指令處理: 設備種類,第幾台,KVM IP,功能指令[,SN]
    # ------------------------------------------------------------------
    def _ensure_kvm(self, ip, timeout=30):
        """確保已連到指定 IP 的 KVM (在 TCP worker 執行緒呼叫, 透過 loop 連線)。
        已連同一 IP 就重用; 否則關舊的、連新的。回傳 JetKVMClient。"""
        if not _KVM_OK:
            raise RuntimeError(f"缺套件: {_KVM_ERR}")
        if self._kvm_loop is None:
            self._kvm_loop = AsyncLoop()
        cur = self._kvm
        if cur and cur.connected and ip in cur.host:
            return cur                              # 重用現有連線
        if cur:
            try:
                self._kvm_loop.submit(cur.close()).result(timeout=10)
            except Exception:
                pass
        client = JetKVMClient(ip)
        self._kvm_loop.submit(client.connect()).result(timeout=timeout)
        self._kvm = client
        self._run_on_ui(lambda: self.btn_kvm.config(text="關閉連接", state="normal"))
        return client

    def _process_command(self, line):
        """解析並執行一條指令, 回傳要回覆的字串。"""
        self._post_log(f"收到指令: {line}")
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            return "error:invalid format (need device,no,KVM_IP,command[,SN])\r\n"
        dev_type, dev_no, kvm_ip, func = parts[0], parts[1], parts[2], parts[3].lower()
        sn = parts[4] if len(parts) > 4 else ""
        try:
            dev_type = self._templates.normalize_device(dev_type)
        except ValueError:
            return f"error:unknown device ({parts[0]})\r\n"

        # 更新「服務端」區的顯示欄位 (設備種類 / 編號 / 指令)
        self._run_on_ui(lambda: (self.var_dev_type.set(dev_type),
                                 self.var_dev_no.set(dev_no),
                                 self.var_func.set(func)))

        # KVM IP 填入欄位並連線
        self._run_on_ui(lambda: (self.e_kvm_ip.delete(0, "end"),
                                 self.e_kvm_ip.insert(0, kvm_ip)))
        try:
            kvm = self._ensure_kvm(kvm_ip)
        except Exception as e:
            return f"error:KVM connect failed ({e or type(e).__name__})\r\n"

        annotate = self._templates.diagnostic_path(dev_type, "cmd_detected.png")
        annotate.parent.mkdir(parents=True, exist_ok=True)
        try:
            if func == "input":
                r = self._kvm_loop.submit(run_flow(
                    kvm, dev_type, template_root=self._templates.root, sn_text=sn, mode="input",
                    log=self._post_log, annotate_path=str(annotate))).result(timeout=60)
            elif func == "button":
                r = self._kvm_loop.submit(run_flow(
                    kvm, dev_type, template_root=self._templates.root, mode="button",
                    log=self._post_log, annotate_path=str(annotate))).result(timeout=60)
            elif func == "check":
                r = self._kvm_loop.submit(run_check(
                    kvm, dev_type, template_root=self._templates.root,
                    log=self._post_log, annotate_path=str(annotate))).result(timeout=60)
                if not r or not r.get("ok"):
                    return "error:window not found\r\n"
                if r.get("testing"):
                    return "action_done,testing\r\n"
                # 每列: index:SN:result (SN 由 OCR 讀取)
                body = ",".join(f"{i}:{sn}:{res}" for i, res, sn in r.get("rows", []))
                return f"action_done,{body}\r\n" if body else "action_done\r\n"
            elif func == "stream":
                self._run_on_ui(self.on_streamer)
                return "action_done\r\n"
            else:
                return f"error:unknown command ({func})\r\n"
        except Exception as e:
            return f"error:execution failed ({e or type(e).__name__})\r\n"

        if not r or not r.get("ok"):
            if r and r.get("hid_error"):
                return f"error:input send failed ({r['hid_error']})\r\n"
            return "error:target not found (low similarity)\r\n"
        return "action_done\r\n"

    def _on_close(self):
        """關閉視窗: 先停掉串流、監聽與 KVM 連線再離開。"""
        try:
            if self._streamer is not None:
                self._streamer.close()
        except Exception:
            pass
        try:
            self._stop_listen()
        except Exception:
            pass
        try:
            if self._kvm and self._kvm.connected and self._kvm_loop:
                self._kvm_loop.submit(self._kvm.close())
                time.sleep(0.3)
        except Exception:
            pass
        try:
            if self._kvm_loop:
                self._kvm_loop.stop()
        except Exception:
            pass
        self.destroy()



if __name__ == "__main__":
    app = AtlasUI()
    app.mainloop()
