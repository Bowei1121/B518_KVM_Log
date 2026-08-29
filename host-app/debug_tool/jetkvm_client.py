# -*- coding: utf-8 -*-
"""
JetKVM 最小可動客戶端 (aiortc + WebSocket signaling)
====================================================
目的: 不開瀏覽器, 直接連 JetKVM 取得遠端畫面 (H.264) 存成 PNG 驗證。

為什麼用 WebSocket 而不是 HTTP /webrtc/session:
  裝置採 trickle ICE — answer 與 ICE 候選都透過 WebSocket 分開送。
  HTTP 端點回的 answer 不含候選, 也沒有管道把裝置候選送來 -> ICE 永遠連不上。
  正解是走 /webrtc/signaling/client 這條 WebSocket。

協定 (從 kvm 原始碼確認):
  WS 連到 {WS}/webrtc/signaling/client
  裝置先送 {"type":"device-metadata", ...}
  我們送   {"type":"offer","data":{"sd": base64(json({"type":"offer","sdp":...}))}}
  裝置回   {"type":"answer","data":"<base64(json(answer))>"}
  雙方互送 {"type":"new-ice-candidate","data":{"candidate":"candidate:...","sdpMid":...,"sdpMLineIndex":...}}
  (我們的候選已包含在 offer SDP 內, 裝置會一併收到, 故我們不必另外 trickle)

安裝: pip install aiortc av opencv-python requests websockets
使用: 改 HOST -> python jetkvm_client.py -> 成功會存出 jetkvm_frame.png
"""
import asyncio
import base64
import json
import sys

import cv2
import requests
import urllib3
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration
from aiortc.sdp import candidate_from_sdp

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ====== 設定 ======
HOST = "http://192.168.132.70"   # ← 你的 JetKVM 裝置位址
PASSWORD = ""                    # ← 有設密碼才填
CAPTURE_SECONDS = 6
OUT_PNG = "jetkvm_frame.png"
# ==================


def login_cookie():
    """有密碼時登入, 回傳要帶進 WS 的 Cookie 字串; noPassword 回 None。"""
    if not PASSWORD:
        print("[i] 未填密碼, 假設 noPassword 模式")
        return None
    r = requests.post(f"{HOST}/auth/login-local", json={"password": PASSWORD},
                      verify=False, timeout=5)
    if r.status_code != 200:
        print(f"[!] 登入失敗 {r.status_code}: {r.text[:150]}")
        return None
    token = r.cookies.get("authToken")
    print("[OK] 登入成功")
    return f"authToken={token}" if token else None


async def wait_ice_complete(pc):
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.Event()

    @pc.on("icegatheringstatechange")
    def _():
        if pc.iceGatheringState == "complete":
            done.set()

    await done.wait()


async def main():
    cookie = login_cookie()

    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pc.createDataChannel("rpc")  # 預留控制通道

    frames = {"count": 0}
    got_frame = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        print(f"[OK] 收到 track: kind={track.kind}")
        if track.kind != "video":
            return

        async def consume():
            while True:
                try:
                    frame = await track.recv()
                except Exception as e:
                    print(f"[!] track 結束: {e}")
                    return
                img = frame.to_ndarray(format="bgr24")
                frames["count"] += 1
                if frames["count"] == 1:
                    print(f"[OK] 第一張影格! 解析度 {img.shape[1]}x{img.shape[0]}")
                    got_frame.set()
                if frames["count"] % 30 == 0 or frames["count"] == 1:
                    cv2.imwrite(OUT_PNG, img)

        asyncio.ensure_future(consume())

    @pc.on("connectionstatechange")
    def _cs():
        print(f"[i] 連線狀態: {pc.connectionState}")

    @pc.on("iceconnectionstatechange")
    def _ics():
        print(f"[i] ICE 狀態: {pc.iceConnectionState}")

    pc.addTransceiver("video", direction="recvonly")

    # 建立 offer 並等候選收齊 (我們的候選會放進 offer SDP)
    await pc.setLocalDescription(await pc.createOffer())
    await wait_ice_complete(pc)
    ld = pc.localDescription
    offer_b64 = base64.b64encode(
        json.dumps({"type": ld.type, "sdp": ld.sdp}).encode()
    ).decode()

    ws_url = (HOST.replace("https://", "wss://").replace("http://", "ws://")
              + "/webrtc/signaling/client")
    headers = {"Cookie": cookie} if cookie else {}

    print(f"[i] 連線 WebSocket: {ws_url}")
    async with websockets.connect(ws_url, additional_headers=headers,
                                  max_size=None) as ws:
        await ws.send(json.dumps({"type": "offer", "data": {"sd": offer_b64}}))
        print("[i] 已送出 offer, 等待 answer / 候選 ...")

        remote_set = asyncio.Event()
        pending = []

        async def add_cand(c):
            if not c or not c.get("candidate"):
                return
            s = c["candidate"]
            if s.startswith("candidate:"):
                s = s[len("candidate:"):]
            ic = candidate_from_sdp(s)
            ic.sdpMid = c.get("sdpMid")
            ic.sdpMLineIndex = c.get("sdpMLineIndex")
            await pc.addIceCandidate(ic)

        async def recv_loop():
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "ignore")
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue  # 忽略 ping/pong 之類純文字
                t = msg.get("type")
                if t == "device-metadata":
                    print(f"[i] 裝置資訊: {msg.get('data')}")
                elif t == "answer":
                    ans = json.loads(base64.b64decode(msg["data"]))
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
                    remote_set.set()
                    for c in pending:
                        await add_cand(c)
                    pending.clear()
                    print("[OK] 已套用 answer")
                elif t == "new-ice-candidate":
                    if remote_set.is_set():
                        await add_cand(msg.get("data"))
                    else:
                        pending.append(msg.get("data"))  # answer 還沒到, 先暫存

        task = asyncio.ensure_future(recv_loop())
        try:
            await asyncio.wait_for(got_frame.wait(), timeout=20)
        except asyncio.TimeoutError:
            print("[!] 20 秒內沒有收到影格, 檢查是否有 RTP 進來 ...")
            try:
                stats = await pc.getStats()
                for s in stats.values():
                    if getattr(s, "type", "") == "inbound-rtp":
                        print(f"   inbound-rtp: packetsReceived="
                              f"{getattr(s, 'packetsReceived', '?')} "
                              f"bytesReceived={getattr(s, 'bytesReceived', '?')}")
                    if getattr(s, "type", "") == "transport":
                        print(f"   transport: bytesReceived="
                              f"{getattr(s, 'bytesReceived', '?')}")
            except Exception as e:
                print(f"   (getStats 失敗: {e})")
        await asyncio.sleep(CAPTURE_SECONDS)
        task.cancel()

    print(f"[done] 共收到 {frames['count']} 張影格, 最後一張存於 {OUT_PNG}")
    await pc.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        sys.exit(1)
