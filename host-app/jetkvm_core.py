# -*- coding: utf-8 -*-
"""
JetKVM 可重用連線模組
=====================
- JetKVMClient(host, password): 建立並保持 WebRTC 連線 (視訊 + rpc 控制)
    .connect() / .close() (coroutine)
    .frame  -> 最新 BGR numpy 影格
    .size   -> (W, H)
    .call(method, params)  -> 送 JSON-RPC (鍵鼠)
    .click(px, py) / .type_text(text)  (coroutine)
- AsyncLoop: 在背景執行緒跑一個 asyncio event loop, 供 Tkinter 等同步程式呼叫
    .submit(coro) -> concurrent.futures.Future

協定細節見 [[jetkvm-direct-client]]: WebSocket signaling + 韌體 0.4.6 走 rpc JSON-RPC。
"""
import asyncio
import base64
import json
import threading
import time
import uuid

import cv2
import numpy as np
import requests
import urllib3
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration
from aiortc.sdp import candidate_from_sdp

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MOD_SHIFT = 0x02
MOD_META = 0x08  # macOS Command / HID Left GUI
_USAGE = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _USAGE[_c] = (False, 0x04 + _i)
    _USAGE[_c.upper()] = (True, 0x04 + _i)
for _i, _c in enumerate("1234567890"):
    _USAGE[_c] = (False, 0x1E + _i)
_USAGE[" "] = (False, 0x2C)
_USAGE["-"] = (False, 0x2D)
_USAGE["_"] = (True, 0x2D)
_USAGE["."] = (False, 0x37)
_USAGE[":"] = (True, 0x33)


class AsyncLoop:
    """背景執行緒裡的 asyncio event loop。"""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        """把 coroutine 丟到背景 loop 執行, 回傳 concurrent.futures.Future。"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


async def grab_one_frame(host, password=""):
    """一次性連線取得一張遠端影格 (BGR numpy) 後關閉。等同 jetkvm_client 的擷取功能。"""
    c = JetKVMClient(host, password)
    try:
        await c.connect()
        return c.frame
    finally:
        await c.close()


class JetKVMClient:
    def __init__(self, host, password=""):
        # host 可為 "192.168.1.50" 或 "http://192.168.1.50"
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        self.host = host.rstrip("/")
        self.password = password
        self.pc = None
        self.rpc = None
        self.frame = None
        self.size = None
        self.frame_sequence = 0
        self.frame_received_monotonic = None
        self.stream_id = uuid.uuid4().hex
        self._frame_lock = threading.Lock()
        self.connected = False
        self._id = 0
        self._ws = None
        self._recv_task = None
        self.rpc_errors = []          # 裝置回傳的 error (例如 HID 寫入失敗)

    # ---- 連線 ----
    def _login_cookie(self):
        if not self.password:
            return None
        r = requests.post(f"{self.host}/auth/login-local",
                          json={"password": self.password}, verify=False, timeout=5)
        if r.status_code == 200:
            return f"authToken={r.cookies.get('authToken')}"
        raise RuntimeError(f"登入失敗 {r.status_code}")

    async def connect(self):
        cookie = self._login_cookie()
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.pc = pc
        self.rpc = pc.createDataChannel("rpc")
        rpc_open = asyncio.Event()
        first_frame = asyncio.Event()

        @self.rpc.on("open")
        def _open():
            rpc_open.set()

        @self.rpc.on("message")
        def _rpc_msg(msg):
            # 記錄裝置回傳的 error (例如 HID 寫入失敗: USB 連線中斷)
            try:
                j = json.loads(msg)
            except Exception:
                return
            if isinstance(j, dict) and j.get("error"):
                err = j["error"]
                detail = err.get("data") or err.get("message") if isinstance(err, dict) else err
                self.rpc_errors.append(str(detail))

        @pc.on("track")
        def on_track(track):
            if track.kind != "video":
                return

            async def consume():
                while True:
                    try:
                        f = await track.recv()
                    except Exception:
                        return
                    decoded = f.to_ndarray(format="bgr24")
                    with self._frame_lock:
                        self.frame = decoded
                        self.frame_sequence += 1
                        self.frame_received_monotonic = time.monotonic()
                        if self.size is None:
                            self.size = (f.width, f.height)
                    if self.size == (f.width, f.height):
                        first_frame.set()

            asyncio.ensure_future(consume())

        pc.addTransceiver("video", direction="recvonly")
        await pc.setLocalDescription(await pc.createOffer())
        await self._wait_ice(pc)
        ld = pc.localDescription
        offer_b64 = base64.b64encode(
            json.dumps({"type": ld.type, "sdp": ld.sdp}).encode()).decode()

        ws_url = (self.host.replace("https://", "wss://").replace("http://", "ws://")
                  + "/webrtc/signaling/client")
        headers = {"Cookie": cookie} if cookie else {}
        # open_timeout 加大: 裝置的 WS 升級握手有時較慢 (預設 10s 會逾時)
        self._ws = await websockets.connect(ws_url, additional_headers=headers,
                                            max_size=None, open_timeout=25)
        await self._ws.send(json.dumps({"type": "offer", "data": {"sd": offer_b64}}))

        remote_set = asyncio.Event()
        pending = []

        async def add_cand(c):
            if not c or not c.get("candidate"):
                return
            s = c["candidate"]
            s = s[len("candidate:"):] if s.startswith("candidate:") else s
            ic = candidate_from_sdp(s)
            ic.sdpMid = c.get("sdpMid")
            ic.sdpMLineIndex = c.get("sdpMLineIndex")
            await pc.addIceCandidate(ic)

        async def recv_loop():
            try:
                async for raw in self._ws:
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8", "ignore")
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if msg.get("type") == "answer":
                        ans = json.loads(base64.b64decode(msg["data"]))
                        await pc.setRemoteDescription(
                            RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
                        remote_set.set()
                        for c in pending:
                            await add_cand(c)
                        pending.clear()
                    elif msg.get("type") == "new-ice-candidate":
                        if remote_set.is_set():
                            await add_cand(msg.get("data"))
                        else:
                            pending.append(msg.get("data"))
            except Exception:
                return

        self._recv_task = asyncio.ensure_future(recv_loop())
        # 先確認控制通道 (rpc) 開啟 -> 代表已連上裝置
        try:
            await asyncio.wait_for(rpc_open.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise RuntimeError("連上網路但裝置未回應 (rpc 未開)")
        # 再等第一張影格; 收不到通常是來源端沒有 HDMI 訊號
        try:
            await asyncio.wait_for(first_frame.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise RuntimeError("連上裝置但收不到畫面 (no_signal): "
                               "請確認來源電腦有輸出 HDMI 到 JetKVM 且螢幕已喚醒")
        await asyncio.sleep(0.3)
        self.connected = True

    def latest_frame(self):
        """Return an immutable-in-practice frame copy with stream freshness metadata."""
        with self._frame_lock:
            if self.frame is None:
                return None
            return (self.frame.copy(), self.frame_sequence,
                    self.frame_received_monotonic, self.stream_id)

    async def _wait_ice(self, pc):
        if pc.iceGatheringState == "complete":
            return
        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def _():
            if pc.iceGatheringState == "complete":
                done.set()

        await done.wait()

    async def close(self):
        self.connected = False
        try:
            if self._recv_task:
                self._recv_task.cancel()
            if self._ws:
                await self._ws.close()
        except Exception:
            pass
        try:
            if self.pc:
                await self.pc.close()
        except Exception:
            pass

    # ---- 輸入 ----
    def call(self, method, params):
        self._id += 1
        self.rpc.send(json.dumps({"jsonrpc": "2.0", "method": method,
                                  "params": params, "id": self._id}))

    def _to_abs(self, px, py):
        w, h = self.size
        return (max(0, min(32767, round(px * 32767 / (w - 1)))),
                max(0, min(32767, round(py * 32767 / (h - 1)))))

    async def click(self, px, py):
        x, y = self._to_abs(px, py)
        self.call("absMouseReport", {"x": x, "y": y, "buttons": 0}); await asyncio.sleep(0.1)
        self.call("absMouseReport", {"x": x, "y": y, "buttons": 1}); await asyncio.sleep(0.1)
        self.call("absMouseReport", {"x": x, "y": y, "buttons": 0}); await asyncio.sleep(0.1)

    async def type_text(self, text):
        for ch in text:
            if ch not in _USAGE:
                continue
            shift, usage = _USAGE[ch]
            self.call("keyboardReport", {"modifier": MOD_SHIFT if shift else 0, "keys": [usage]})
            await asyncio.sleep(0.02)
            self.call("keyboardReport", {"modifier": 0, "keys": []})
            await asyncio.sleep(0.02)

    async def press_key(self, usage, modifier=0):
        """送單一按鍵 (按下->放開)。usage 為 HID usage code。"""
        self.call("keyboardReport", {"modifier": modifier, "keys": [usage]})
        await asyncio.sleep(0.04)
        self.call("keyboardReport", {"modifier": 0, "keys": []})
        await asyncio.sleep(0.04)

    async def press_enter(self):
        await self.press_key(0x28)   # HID usage: Enter

    async def press_command_shift_m(self):
        """Trigger the independent macOS Log monitor shortcut."""
        await self.press_key(0x10, MOD_META | MOD_SHIFT)  # HID usage: M
