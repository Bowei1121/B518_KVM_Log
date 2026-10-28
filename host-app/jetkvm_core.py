# -*- coding: utf-8 -*-
"""
JetKVM 可重用連線模組
=====================
- JetKVMClient(host, password): 建立並保持 WebRTC 連線 (視訊 + rpc 控制)
    .connect() / .close() (coroutine)
    .latest_frame() -> FrameSnapshot (最新 BGR 影格的副本與新鮮度; 尚無影格時 None)
    .is_healthy() -> bool (影像軌道結束或 peer connection failed/closed 時為 False)
    .size   -> (W, H)
    .call(method, params)  -> 送 JSON-RPC (鍵鼠)
    .click(px, py) / .type_text(text)  (coroutine)

協定細節見 [[jetkvm-direct-client]]: WebSocket signaling + 韌體 0.4.6 走 rpc JSON-RPC。
"""
import asyncio
import base64
import json
import logging
import threading
import time
import uuid
from typing import Optional

import cv2
import numpy as np
import requests
import urllib3
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration
from aiortc.sdp import candidate_from_sdp

from frame_snapshot import FrameSnapshot

logger = logging.getLogger("JetKVMClient")

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


class KVMLoginFailed(RuntimeError):
    reason = "login_failed"


class KVMRpcNotOpen(RuntimeError):
    reason = "rpc_not_open"


class KVMNoSignal(RuntimeError):
    reason = "no_signal"


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
        self.frame_presentation_time = None
        self.stream_id = uuid.uuid4().hex
        self._frame_lock = threading.Lock()
        self.connected = False
        # Death signals (see is_healthy): the video track ended, or the peer connection state.
        self._track_ended = False
        self._peer_state = "new"
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
        raise KVMLoginFailed(f"登入失敗 {r.status_code}")

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

        self._watch_session(pc, first_frame)

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
            raise KVMRpcNotOpen("連上網路但裝置未回應 (rpc 未開)")
        # 再等第一張影格; 收不到通常是來源端沒有 HDMI 訊號
        try:
            await asyncio.wait_for(first_frame.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise KVMNoSignal("連上裝置但收不到畫面 (no_signal): "
                               "請確認來源電腦有輸出 HDMI 到 JetKVM 且螢幕已喚醒")
        await asyncio.sleep(0.3)
        self.connected = True

    def _watch_session(self, pc, first_frame):
        """Wire the two death signals to the peer connection: its state, and the end of the video track."""
        @pc.on("connectionstatechange")
        def _peer_state_changed():
            self._on_peer_state(pc.connectionState)

        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                asyncio.ensure_future(self._pump_video(track, first_frame))

    async def _pump_video(self, track, first_frame=None):
        """Decode the video track into self.frame until it ends. The end of the track is a death signal."""
        while True:
            try:
                f = await track.recv()
            except Exception as exc:
                self._track_ended = True
                logger.warning("KVM video track ended (host=%s): %s", self.host, exc,
                               extra={"zh": "KVM 影像軌道結束 (host=%s): %s"})
                return
            decoded = f.to_ndarray(format="bgr24")
            presentation_time = None
            if f.pts is not None and f.time_base is not None:
                presentation_time = float(f.pts * f.time_base)
            with self._frame_lock:
                self.frame = decoded
                self.frame_sequence += 1
                self.frame_received_monotonic = time.monotonic()
                self.frame_presentation_time = presentation_time
                if self.size is None:
                    self.size = (f.width, f.height)
            if first_frame is not None and self.size == (f.width, f.height):
                first_frame.set()

    def _on_peer_state(self, state):
        self._peer_state = state
        if state in ("failed", "closed"):
            logger.warning("KVM peer connection %s (host=%s)", state, self.host,
                           extra={"zh": "KVM 對等連線 %s (host=%s)"})

    def is_healthy(self) -> bool:
        """False only when the session is dead: the video track ended, or the peer connection failed or closed.

        A stale picture does not count; Freeze Guard answers that with frame_frozen. aiortc never
        emits a "disconnected" state, and any state other than failed/closed is treated as alive.
        """
        return self.connected and not self._track_ended and self._peer_state not in ("failed", "closed")

    def unhealthy_reason(self) -> str:
        """Why is_healthy() is False: not_connected, track_ended, peer_failed or peer_closed; "" when healthy."""
        if not self.connected:
            return "not_connected"
        if self._track_ended:
            return "track_ended"
        if self._peer_state in ("failed", "closed"):
            return "peer_" + self._peer_state
        return ""

    def latest_frame(self) -> Optional[FrameSnapshot]:
        """The newest picture as a copy with its freshness metadata; None until the first frame."""
        with self._frame_lock:
            if self.frame is None:
                return None
            return FrameSnapshot(self.frame.copy(), self.frame_sequence,
                                 self.frame_received_monotonic, self.stream_id,
                                 self.frame_presentation_time)

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

    async def move(self, px, py):
        """Move the pointer without clicking."""
        x, y = self._to_abs(px, py)
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
