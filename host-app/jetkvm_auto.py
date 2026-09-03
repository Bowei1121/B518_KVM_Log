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
import sys

import cv2

from jetkvm_core import JetKVMClient
from auto_flow import run_focus
from template_catalog import TemplateCatalog

# ====== 設定 ======
HOST = "http://192.168.132.70"
PASSWORD = ""
DRY_RUN = True            # True=只偵測標註不點擊; False=實際執行點擊與輸入
SN_TEXT = "SN_ABC"
THRESHOLD = 0.6
DEVICE = "FCT"
TEMPLATES = TemplateCatalog()
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

    # 原始擷取圖不屬於正式模板，集中存到 Documents/template/_captures。
    if kvm.frame is not None:
        capture = TEMPLATES.capture_path()
        capture.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(capture), kvm.frame)

    if DRY_RUN:
        print("[DRY_RUN] FCT 不送 HID；請在 GUI 按 Switch 驗證 Dock 前景化。")
    else:
        await run_focus(kvm, DEVICE, template_root=TEMPLATES.root, threshold=THRESHOLD, log=print)

    if DRY_RUN:
        print("[DRY_RUN] 確認 FCT_dock_icon.png 正確後，再把 DRY_RUN 改 False。")

    await kvm.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        sys.exit(1)
