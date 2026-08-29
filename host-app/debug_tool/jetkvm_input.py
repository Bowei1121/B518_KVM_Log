# -*- coding: utf-8 -*-
"""
JetKVM 輸入驗證 (B): 透過 rpc data channel 的 JSON-RPC 直接送鍵鼠到遠端
=====================================================================
不靠瀏覽器、不靠 pyautogui。

韌體 0.4.6 的輸入走 "rpc" 通道的 JSON-RPC (二進位 hidrpc 是新版才有):
  absMouseReport {x, y, buttons}   x,y 為 0..32767 對應整個螢幕; buttons bit0=左鍵
  keyboardReport {modifier, keys}  keys=HID usage code 陣列; 放開送 keys=[]
  modifier bit1 (0x02) = 左 Shift

預設只「移動游標畫方框」(安全)。確認 OK 再開 DO_CLICK / DO_TYPE。
安裝: pip install aiortc av opencv-python requests websockets
使用: 改 HOST -> python jetkvm_input.py
"""
import asyncio
import base64
import json
import sys

import requests
import urllib3
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration
from aiortc.sdp import candidate_from_sdp

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ====== 設定 ======
HOST = "http://192.168.132.70"
PASSWORD = ""
DO_MOVE = True
DO_CLICK = False
DO_TYPE = False
# ==================

MOD_SHIFT = 0x02
_USAGE = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _USAGE[c] = (False, 0x04 + i)
    _USAGE[c.upper()] = (True, 0x04 + i)
for i, c in enumerate("1234567890"):
    _USAGE[c] = (False, 0x1E + i)
_USAGE[" "] = (False, 0x2C)
_USAGE["-"] = (False, 0x2D)
_USAGE["_"] = (True, 0x2D)
_USAGE["."] = (False, 0x37)
_USAGE[":"] = (True, 0x33)


async def login_cookie():
    if not PASSWORD:
        return None
    r = requests.post(f"{HOST}/auth/login-local", json={"password": PASSWORD},
                      verify=False, timeout=5)
    return f"authToken={r.cookies.get('authToken')}" if r.status_code == 200 else None


async def wait_ice_complete(pc):
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.Event()

    @pc.on("icegatheringstatechange")
    def _():
        if pc.iceGatheringState == "complete":
            done.set()

    await done.wait()


class JetKVM:
    def __init__(self):
        self.pc = None
        self.rpc = None
        self.size = None
        self._id = 0
        self._first_frame = asyncio.Event()
        self._rpc_open = asyncio.Event()

    def _call(self, method, params):
        """送一個 JSON-RPC 請求 (鍵鼠都走這裡)。"""
        self._id += 1
        self.rpc.send(json.dumps({
            "jsonrpc": "2.0", "method": method, "params": params, "id": self._id}))

    async def connect(self):
        cookie = await login_cookie()
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.pc = pc

        self.rpc = pc.createDataChannel("rpc")

        @self.rpc.on("open")
        def _open():
            print(f"[OK] rpc 通道已開 (id={self.rpc.id})")
            self._rpc_open.set()

        @self.rpc.on("message")
        def _msg(message):
            # 只印出有 id 的回覆 (鍵鼠的 ack), 事件略過
            try:
                m = json.loads(message)
                if "id" in m:
                    print(f"[rpc<-裝置] {message}")
            except Exception:
                pass

        @pc.on("track")
        def on_track(track):
            if track.kind != "video":
                return

            async def consume():
                while True:
                    try:
                        frame = await track.recv()
                    except Exception:
                        return
                    if self.size is None:
                        self.size = (frame.width, frame.height)
                        print(f"[OK] 遠端解析度 {frame.width}x{frame.height}")
                        self._first_frame.set()

            asyncio.ensure_future(consume())

        @pc.on("connectionstatechange")
        def _cs():
            print(f"[i] 連線狀態: {pc.connectionState}")

        pc.addTransceiver("video", direction="recvonly")
        await pc.setLocalDescription(await pc.createOffer())
        await wait_ice_complete(pc)
        ld = pc.localDescription
        offer_b64 = base64.b64encode(
            json.dumps({"type": ld.type, "sdp": ld.sdp}).encode()).decode()

        ws_url = (HOST.replace("https://", "wss://").replace("http://", "ws://")
                  + "/webrtc/signaling/client")
        headers = {"Cookie": cookie} if cookie else {}
        self._ws = await websockets.connect(ws_url, additional_headers=headers, max_size=None)
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
            async for raw in self._ws:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "ignore")
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                t = msg.get("type")
                if t == "answer":
                    ans = json.loads(base64.b64decode(msg["data"]))
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
                    remote_set.set()
                    for c in pending:
                        await add_cand(c)
                    pending.clear()
                elif t == "new-ice-candidate":
                    if remote_set.is_set():
                        await add_cand(msg.get("data"))
                    else:
                        pending.append(msg.get("data"))

        self._recv_task = asyncio.ensure_future(recv_loop())
        await asyncio.wait_for(self._first_frame.wait(), timeout=20)
        await asyncio.wait_for(self._rpc_open.wait(), timeout=10)

    def _to_abs(self, px, py):
        w, h = self.size
        x = max(0, min(32767, round(px * 32767 / (w - 1))))
        y = max(0, min(32767, round(py * 32767 / (h - 1))))
        return x, y

    async def move(self, px, py):
        x, y = self._to_abs(px, py)
        self._call("absMouseReport", {"x": x, "y": y, "buttons": 0})
        await asyncio.sleep(0.05)

    async def click(self, px, py):
        x, y = self._to_abs(px, py)
        self._call("absMouseReport", {"x": x, "y": y, "buttons": 0}); await asyncio.sleep(0.08)
        self._call("absMouseReport", {"x": x, "y": y, "buttons": 1}); await asyncio.sleep(0.08)
        self._call("absMouseReport", {"x": x, "y": y, "buttons": 0}); await asyncio.sleep(0.08)

    async def type_text(self, text):
        for ch in text:
            if ch not in _USAGE:
                continue
            shift, usage = _USAGE[ch]
            self._call("keyboardReport", {"modifier": MOD_SHIFT if shift else 0, "keys": [usage]})
            await asyncio.sleep(0.03)
            self._call("keyboardReport", {"modifier": 0, "keys": []})
            await asyncio.sleep(0.03)

    async def close(self):
        try:
            self._recv_task.cancel()
            await self._ws.close()
        except Exception:
            pass
        await self.pc.close()


async def main():
    kvm = JetKVM()
    await kvm.connect()
    w, h = kvm.size
    print(f"[i] 開始輸入測試 (解析度 {w}x{h})")

    if DO_MOVE:
        print("[demo] 移動游標畫一個方框, 請看遠端螢幕的滑鼠")
        for px, py in [(w*0.3, h*0.3), (w*0.7, h*0.3), (w*0.7, h*0.7),
                       (w*0.3, h*0.7), (w*0.5, h*0.5)]:
            await kvm.move(px, py)
            await asyncio.sleep(0.6)

    if DO_CLICK:
        print("[demo] 在畫面中央點一下左鍵")
        await kvm.click(w*0.5, h*0.5)

    if DO_TYPE:
        print("[demo] 輸入 SN_ABC")
        await kvm.type_text("SN_ABC")

    await asyncio.sleep(0.5)
    print("[done] 輸入測試結束")
    await kvm.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        sys.exit(1)
