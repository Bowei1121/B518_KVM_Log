# -*- coding: utf-8 -*-
"""
JetKVM 全自動流程 (CLI) — 與 GUI 共用同一份邏輯
================================================
連線 (jetkvm_core.JetKVMClient) + 比對/輸入 (auto_flow.run_flow)。
比對與輸入邏輯都在 auto_flow.py, 與 ui_app.py 的「Switch」按鈕共用同一份。

安全: 預設 DRY_RUN=True, 只偵測並輸出標註圖 jetkvm_detected.png, 不點擊。
      確認框框正確後, 把 DRY_RUN 改 False 才會實際執行。

安裝: pip install aiortc av opencv-python numpy requests websockets
使用: 改 HOST -> python jetkvm_auto.py
"""
import asyncio
import os
import sys

import cv2

from jetkvm_core import JetKVMClient
from auto_flow import run_flow

# ====== 設定 ======
HOST = "http://192.168.132.70"
PASSWORD = ""
DRY_RUN = True            # True=只偵測標註不點擊; False=實際執行點擊與輸入
SN_TEXT = "SN_ABC"
THRESHOLD = 0.6
FCT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FCT")
# ==================


async def main():
    kvm = JetKVMClient(HOST, PASSWORD)
    print("[i] 連線中 ...")
    try:
        await kvm.connect()
    except Exception as e:
        print(f"[X] 連線失敗: {type(e).__name__}: {e}")
        return
    print(f"[OK] 已連線, 遠端 {kvm.size[0]}x{kvm.size[1]}")

    # 存一張乾淨影格 (供 crop_template / 創建Pattern 使用)
    if kvm.frame is not None:
        cv2.imwrite("jetkvm_frame.png", kvm.frame)

    await run_flow(kvm, FCT_DIR, sn_text=SN_TEXT, threshold=THRESHOLD,
                   log=print, annotate_path="jetkvm_detected.png",
                   do_action=not DRY_RUN)

    if DRY_RUN:
        print("[DRY_RUN] 只偵測不動作。確認 jetkvm_detected.png 正確後, 把 DRY_RUN 改 False。")

    await kvm.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        sys.exit(1)
