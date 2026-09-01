# FCT 設備工作流程

更新日期：2026-09-01（Asia/Taipei）

## 文件定位與責任邊界

本文件是 FCT 設備畫面操作與測試結果判讀的單一來源。本 repo 以 JetKVM 串流畫面作為 FCT 狀態與 PASS／FAIL 的正式依據，並在需要時透過 JetKVM USB HID 操作測試機。

測試機本地 Log、CSV、active 目錄與 archive 目錄均由另一個專案負責。本 repo 不讀取、不監聽、不解析，也不與該專案交換資料。

## 前置條件

- JetKVM HDMI IN 已連到 FCT 測試機畫面，USB HID 已連到測試機。
- 上位機已建立 JetKVM WebRTC 連線，並持續取得最新影格。
- FCT profile 至少提供下列畫面樣板：
  - target.png：可穩定定位的 FCT 視窗區域。
  - Testing_target.png：測試進行中的狀態。
  - Pass_target.png、Fail_target.png：單列測試結果。
- 若需要由上位機輸入 SN 或啟動測試，另提供 Input_target.png 與 Button_target.png。

## 畫面操作流程

1. 取得最新 JetKVM 影格，使用 target.png 定位 FCT 視窗。
2. 輸入 SN 時，定位 Input_target.png、點擊輸入區、送出 SN 與 Enter。
3. 啟動測試時，定位 Button_target.png 並點擊；只有前置目標辨識成功時才允許操作。
4. 每次 HID 動作後檢查 JetKVM RPC 是否回報 USB HID endpoint 錯誤。
5. 視窗、輸入框或按鈕未命中時，停止該步驟並回報錯誤，不依固定座標盲點。

## 視覺結果判讀

1. 使用 target.png 定位 FCT 視窗；若視窗未命中，回覆視覺目標未找到。
2. 在視窗範圍搜尋 Testing_target.png。命中時，結果為測試中，回覆 action_done,testing。
3. 未命中 Testing 時，搜尋所有 Pass_target.png 與 Fail_target.png。
4. 依畫面垂直位置將所有 PASS／FAIL 標記由上而下排序。
5. 將視窗寬度 22%～51% 的區域作為 SN 欄，使用 OCR 一次讀取所有可見 SN，再以垂直位置配對結果列。
6. 回覆 action_done,index:SN:pass|fail,...。這是本 repo 的正式視覺判讀結果，不等待或合併機台本地 Log。
7. 若找不到結果標記，回覆 action_done，表示已辨識到視窗但目前沒有可判讀的 PASS／FAIL 列。

## 錯誤處理與診斷

| 情況 | 處理 |
|---|---|
| 無影格 | 回報 no_signal，不送 HID |
| 視窗或操作目標低於門檻 | 回報低相似度錯誤，保留辨識疊圖 |
| HID endpoint 錯誤 | 回報輸入失敗，檢查 JetKVM 到測試機的 USB 線與喚醒狀態 |
| OCR 未配對到 SN | 仍回傳該列結果，SN 以目前程式的預設值 SN 表示未辨識到可用條碼 |
| PASS／FAIL 同列重疊或順序不明 | 保留疊圖供人工確認；在 pattern 或 OCR 規則調整前，不加入推測性 slot 對應 |

## 目前實作狀態

- host-app/auto_flow.py 已實作本文件的視窗定位、Testing 判讀、PASS／FAIL 多列搜尋、OCR SN 配對、通用輸入與按鈕操作。
- host-app/FCT 已有上述 FCT 樣板與辨識疊圖範例。
- check 是純讀取畫面，不會操作 HID；input 與 button 分別執行單筆 SN 輸入與按鈕點擊。

## 驗收準則

- 在實機畫面連續驗證 Testing、全 PASS、全 FAIL、混合 PASS／FAIL、無結果列與畫面無訊號。
- 驗證多列結果的畫面順序、SN OCR 與結果列配對。
- 驗證視窗縮放、移動、字型差異及 pattern 更新後仍能在門檻內辨識。
- 任一視覺目標或 HID 動作失敗時，程式必須停止後續動作並輸出可追查的辨識疊圖。
