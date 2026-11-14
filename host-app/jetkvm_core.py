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
from task_supervisor import supervise_background
import base64
import json
import logging
import os
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


# YUV->BGR matrix for JetKVM frames. The JetKVM encodes BT.601 but does not mark the stream as such, so
# decoding with the frame's own (BT.709 / unmarked) tag shifts PASS green (0,239,0) to about (0,199,0), past
# the reader's colour tolerance. "auto" follows the frame's own tag (the old behaviour).
COLORSPACE_ENV = "JETKVM_DECODE_COLORSPACE"
DEFAULT_DECODE_COLORSPACE = "ITU601"
DECODE_COLORSPACES = ("ITU601", "ITU709", "auto")


def resolve_decode_colorspace(value=None):
    """Normalize a decode colour matrix setting; None reads ``JETKVM_DECODE_COLORSPACE``.

    Allowed: ITU601, ITU709, auto (case-insensitive). An invalid value falls back to the default with a
    warning (a typo must not take Core down on the day of the line check).
    """
    raw = os.environ.get(COLORSPACE_ENV) if value is None else value
    if raw is None or not str(raw).strip():
        return DEFAULT_DECODE_COLORSPACE
    wanted = str(raw).strip().lower()
    for allowed in DECODE_COLORSPACES:
        if allowed.lower() == wanted:
            return allowed
    logger.warning("Invalid decode colorspace %r; using %s (allowed: %s)", raw, DEFAULT_DECODE_COLORSPACE,
                   ", ".join(DECODE_COLORSPACES),
                   extra={"zh": "解碼色彩空間設定 %r 無效，改用 %s（可用值：%s）"})
    return DEFAULT_DECODE_COLORSPACE


# PyAV reports frame.colorspace / color_range as the ffmpeg integers (AVColorSpace / AVColorRange).
_FFMPEG_COLORSPACES = {0: "RGB", 1: "ITU709", 2: "UNSPECIFIED", 4: "FCC", 5: "ITU470BG", 6: "SMPTE170M",
                       7: "SMPTE240M", 8: "YCGCO", 9: "BT2020_NCL", 10: "BT2020_CL"}
_FFMPEG_RANGES = {0: "UNSPECIFIED", 1: "MPEG", 2: "JPEG"}


def colorspace_name(value, ranges=False):
    """Readable name of a frame's colorspace (or color_range with ``ranges=True``); unknown values stay numeric."""
    if value is None:
        return "None"
    name = getattr(value, "name", None)
    if name:
        return str(name)
    table = _FFMPEG_RANGES if ranges else _FFMPEG_COLORSPACES
    try:
        return table.get(int(value), str(int(value)))
    except (TypeError, ValueError):
        return str(value)


def decode_frame_bgr(frame, src_colorspace):
    """One av.VideoFrame as a BGR array. ``auto`` follows the frame's own tag; otherwise the matrix is forced."""
    if src_colorspace == "auto":
        return frame.to_ndarray(format="bgr24")
    return frame.reformat(format="bgr24", src_colorspace=src_colorspace).to_ndarray()


MANUAL_ID_BASE = 1_000_000_000  # operator / release RPCs use ids >= this; Core's automatic ones stay below
CONFIRM_TIMEOUT_SEC = 1.0


class JetKVMClient:
    def __init__(self, host, password="", src_colorspace=None):
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
        self.src_colorspace = resolve_decode_colorspace(src_colorspace)  # decode_colorspace of core_status
        self.frame_colorspace = None  # the newest frame's own colorspace tag, updated with the image
        self._first_frame_logged = False
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
        self._manual_id = MANUAL_ID_BASE  # 手動 HID 專用 id 空間: 回覆以 id 範圍歸屬, 不靠會過期的表
        self._confirm = {}            # id -> (loop, future): 等待回覆的釋放 RPC

    # ---- 連線 ----
    def _login_cookie(self):
        if not self.password:
            return None
        r = requests.post(f"{self.host}/auth/login-local",
                          json={"password": self.password}, verify=False, timeout=5)
        if r.status_code == 200:
            return f"authToken={r.cookies.get('authToken')}"
        raise KVMLoginFailed(f"Login failed (HTTP {r.status_code})")

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

        self.rpc.on("message")(self._on_rpc_message)

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

        self._recv_task = supervise_background(asyncio.ensure_future(recv_loop()))
        # 先確認控制通道 (rpc) 開啟 -> 代表已連上裝置
        try:
            await asyncio.wait_for(rpc_open.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise KVMRpcNotOpen("Network connected but device did not respond (RPC channel not open)")
        # 再等第一張影格; 收不到通常是來源端沒有 HDMI 訊號
        try:
            await asyncio.wait_for(first_frame.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise KVMNoSignal("Device connected but no video received (no_signal): "
                               "check that the source computer outputs HDMI to JetKVM and the display is awake")
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
                supervise_background(asyncio.ensure_future(self._pump_video(track, first_frame)))

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
            decoded = decode_frame_bgr(f, self.src_colorspace)
            tag = colorspace_name(f.colorspace)
            if not self._first_frame_logged:
                self._first_frame_logged = True
                logger.info("First video frame (host=%s): colorspace=%s color_range=%s format=%s; decoding with %s",
                            self.host, tag, colorspace_name(f.color_range, ranges=True), f.format.name,
                            self.src_colorspace,
                            extra={"zh": "第一張影格 (host=%s)：colorspace=%s color_range=%s format=%s；解碼採用 %s"})
            presentation_time = None
            if f.pts is not None and f.time_base is not None:
                presentation_time = float(f.pts * f.time_base)
            with self._frame_lock:
                self.frame = decoded
                self.frame_colorspace = tag  # same lock as the image: a snapshot never mixes frame and tag
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
                                 self.frame_presentation_time, self.frame_colorspace, self.src_colorspace)

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
    def _on_rpc_message(self, msg):
        # 記錄裝置回傳的 error (例如 HID 寫入失敗: USB 連線中斷)
        try:
            j = json.loads(msg)
        except Exception:
            return
        rid = j.get("id") if isinstance(j, dict) else None
        if isinstance(rid, int) and rid >= MANUAL_ID_BASE:
            # Operator / release reply, however late: never a flow's hid_failed.
            waiter = self._confirm.pop(rid, None)
            if waiter is not None:
                err = j.get("error")
                detail = (err.get("data") or err.get("message")) if isinstance(err, dict) else err
                waiter[0].call_soon_threadsafe(waiter[1].set_result, str(detail) if err else None)
            return
        if isinstance(j, dict) and j.get("error"):
            err = j["error"]
            detail = err.get("data") or err.get("message") if isinstance(err, dict) else err
            self.rpc_errors.append(str(detail))

    def call(self, method, params):
        self._id += 1
        self._send(method, params, self._id)

    def _send(self, method, params, rid):
        self.rpc.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": rid}))

    def call_manual(self, method, params):
        """Like ``call`` for operator HID: its reply (even a late error) is kept out of ``rpc_errors``."""
        self._manual_id += 1
        self._send(method, params, self._manual_id)

    async def call_confirmed(self, method, params, timeout=None):
        """Send in the manual id space and wait (bounded) for the device's reply; RuntimeError on error / timeout."""
        timeout = CONFIRM_TIMEOUT_SEC if timeout is None else timeout
        self._manual_id += 1
        rid = self._manual_id
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._confirm[rid] = (loop, fut)
        try:
            self._send(method, params, rid)
            detail = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"{method} was not confirmed within {timeout}s") from None
        finally:
            self._confirm.pop(rid, None)
        if detail is not None:
            raise RuntimeError(f"{method} failed: {detail}")

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

    async def park(self, px, py):
        """``move`` for resting the pointer after a click: its reply, even a late error, never reaches ``rpc_errors``.

        A click's own reply errors must stay attributable to that click, so the park uses the manual id space.
        """
        x, y = self._to_abs(px, py)
        self.call_manual("absMouseReport", {"x": x, "y": y, "buttons": 0}); await asyncio.sleep(0.1)

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
