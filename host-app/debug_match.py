# -*- coding: utf-8 -*-
"""
雙螢幕比對診斷工具

用途: 確認 target.png 在哪個螢幕、以什麼縮放比例可被比對到。
請先把目標視窗開在「第二螢幕」, 再執行:  python debug_match.py

會做三件事:
1. 列出 mss 看到的所有螢幕
2. 把每個螢幕各自存成 debug_monitor_N.png 供肉眼確認
3. 對每個螢幕做「多尺度」範本比對, 印出最高相似度與座標
"""
import os
import sys

try:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import cv2
import numpy as np
import mss


def get_resource_path(rel):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def main():
    tpath = get_resource_path("target.png")
    template = cv2.imread(tpath)
    if template is None:
        print(f"[X] 讀不到 target.png: {tpath}")
        return
    th, tw = template.shape[:2]
    print(f"[i] target.png 尺寸: {tw}x{th}")

    # 多尺度: 模擬不同 DPI 縮放 (0.5 ~ 2.0 倍)
    scales = [0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

    with mss.mss() as sct:
        print("\n[i] mss 偵測到的螢幕:")
        for i, m in enumerate(sct.monitors):
            tag = "虛擬桌面(全部)" if i == 0 else f"螢幕 {i}"
            print(f"    [{i}] {tag}: {m}")

        # 逐螢幕 (跳過 index 0 的虛擬桌面)
        for i in range(1, len(sct.monitors)):
            mon = sct.monitors[i]
            raw = sct.grab(mon)
            img = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)

            out = f"debug_monitor_{i}.png"
            cv2.imwrite(out, img)
            print(f"\n===== 螢幕 {i} ({mon['width']}x{mon['height']}) 已存檔: {out} =====")

            best = {"val": -1, "scale": None, "loc": None, "size": None}
            for s in scales:
                nw, nh = int(tw * s), int(th * s)
                if nw < 8 or nh < 8 or nw > img.shape[1] or nh > img.shape[0]:
                    continue
                tmpl = cv2.resize(template, (nw, nh), interpolation=cv2.INTER_AREA)
                res = cv2.matchTemplate(img, tmpl, cv2.TM_CCOEFF_NORMED)
                _, mx, _, mloc = cv2.minMaxLoc(res)
                if mx > best["val"]:
                    best = {"val": mx, "scale": s, "loc": mloc, "size": (nw, nh)}

            if best["scale"] is None:
                print("    (此螢幕沒有任何尺度可比對, 範本可能比螢幕還大)")
                continue

            # 換算成絕對座標
            cx = mon["left"] + best["loc"][0] + best["size"][0] // 2
            cy = mon["top"] + best["loc"][1] + best["size"][1] // 2
            mark = "  <-- 很可能是這個!" if best["val"] >= 0.7 else ""
            print(f"    最高相似度: {best['val']:.3f}  最佳縮放: {best['scale']}x"
                  f"  絕對座標: ({cx}, {cy}){mark}")


if __name__ == "__main__":
    main()
