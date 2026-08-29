import cv2
import numpy as np
import pyautogui
import time

import os
import sys

def get_resource_path(relative_path):
    """ 取得 PyInstaller 打包後的正確檔案路徑 """
    if hasattr(sys, '_MEIPASS'):
        # 如果是打包後的環境，讀取臨時資料夾
        return os.path.join(sys._MEIPASS, relative_path)
    # 👉 本機開發環境：改用 __file__ 精確定位 test_kyle.py 所在的資料夾
    base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def find_and_click():
    # 1. 擷取目前螢幕畫面
    screenshot = pyautogui.screenshot()
    screen_np = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
    
    # 2. 讀取你要尋找的目標圖片 (例如: 按鈕)
    # template = cv2.imread(r"D:\482_Tool\Monitor_Screen\Screen_pic\target.png")
    # 👉 修改原本的圖片讀取方式：
    template_path = get_resource_path("target.png")
    print(f"🔍 程式正在嘗試讀取此路徑的圖片: {template_path}")
    template = cv2.imread(template_path)
    h, w, _ = template.shape

    template_path = get_resource_path("target.png")

    
    # 3. 進行影像範本比對
    result = cv2.matchTemplate(screen_np, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    
    # 設定辨識閥值 (例如: 相似度大於 85%)
    threshold = 0.85
    
    if max_val >= threshold:
        # 4. 計算目標中心的 X, Y 座標
        top_left = max_loc
        center_x = top_left[0] + w // 2
        center_y = top_left[1] + h // 2
        
        print(f"成功識別目標！相似度: {max_val:.2f}，座標: ({center_x}, {center_y})")
        
        # 5. 模擬人類操作：平滑移動過去並點擊
        pyautogui.moveTo(center_x, center_y, duration=0.5)
        pyautogui.click()
        
        # 6. 模擬鍵盤操作
        time.sleep(0.5)
        pyautogui.write("Success!", interval=0.1)
        pyautogui.press('enter')
    else:
        print("未在螢幕上找到指定目標。")

# 執行腳本
if __name__ == "__main__":
    print("3秒後開始執行，請切換到目標視窗...")
    time.sleep(3)
    find_and_click()
