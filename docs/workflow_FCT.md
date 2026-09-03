# FCT 設備工作流程

更新日期：2026-09-03（Asia/Shanghai）

## 文件定位與責任邊界

本文件是 FCT 設備畫面操作與測試結果判讀的單一來源。本 repo 以 JetKVM 串流畫面作為 FCT 狀態與 PASS／FAIL 的正式依據，並在需要時透過 JetKVM USB HID 操作測試機。

測試機本地 Log、CSV、active 目錄與 archive 目錄均由另一個專案負責。本 repo 不讀取、不監聽、不解析，也不與該專案交換資料。

## 前置條件

- JetKVM HDMI IN 已連到 FCT 測試機畫面，USB HID 已連到測試機。
- 上位機已建立 JetKVM WebRTC 連線，並持續取得最新影格。
- FCT 樣板固定存於 `~/Documents/template/FCT/`：
  - `FCT_window.png`：可穩定定位的 FCT 視窗區域。
  - `FCT_testing.png`：測試進行中的狀態。
  - `FCT_pass.png`、`FCT_fail.png`：單列測試結果。
  - `FCT_dock_icon.png`：Dock 上的 FCT 測試程式 icon，用於前景化。

## 畫面操作流程

1. 主畫面 Switch 取得最新 JetKVM 影格，使用 `FCT_dock_icon.png` 定位並點擊 FCT 程式 icon。
2. 點擊後等待 0.5 秒讓 HMI 回到最前方；Dock 未命中或 HID 回報錯誤時立即停止。
3. Switch 不輸入 SN、不點擊測試按鈕。
4. FCT 的 TCP `input` 與 `button` 指令明確不支援，不建立 KVM 連線也不送 HID。

## 視覺結果判讀

1. 使用 `FCT_window.png` 定位 FCT 視窗；若視窗未命中，回覆視覺目標未找到。
2. 在視窗範圍搜尋 `FCT_testing.png`。命中時，結果為測試中，回覆 `action_done,testing`。
3. 未命中 Testing 時，搜尋所有 `FCT_pass.png` 與 `FCT_fail.png`。
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

- host-app/auto_flow.py 已實作本文件的視窗定位、Testing 判讀、PASS／FAIL 多列搜尋、OCR SN 配對與 FCT Dock 前景化。
- `host-app/FCT` 的舊圖片只作歷史參考；執行時只讀取 `~/Documents/template/FCT/`。
- check 是純讀取畫面，不會操作 HID；FCT input 與 button 指令會回覆不支援。
- Switch 的 Dock 比對結果保存在 `~/Documents/template/_captures/match_diagnostics/FCT/latest/`，可由主畫面的「匹配結果」檢視。

## 驗收準則

- 在實機畫面連續驗證 Testing、全 PASS、全 FAIL、混合 PASS／FAIL、無結果列與畫面無訊號。
- 驗證多列結果的畫面順序、SN OCR 與結果列配對。
- 驗證視窗縮放、移動、字型差異及 pattern 更新後仍能在門檻內辨識。
- 任一視覺目標或 HID 動作失敗時，程式必須停止後續動作並輸出可追查的辨識疊圖。
