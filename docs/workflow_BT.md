# BT 設備工作流程

更新日期：2026-09-01（Asia/Taipei）

## 文件定位與責任邊界

本文件是 BT 設備畫面操作與測試結果判讀的單一來源。本 repo 使用 JetKVM 串流畫面定位 BT HMI、輸入 SN、操作 slot 與按鈕，並以畫面上的 Testing／PASS／FAIL 作為正式視覺結果。

測試機本地 Log、CSV、Thread 與測試資料目錄由另一個專案負責。本 repo 不讀取、不監聽、不解析，也不將其納入結果仲裁。

## 已確認條件

- BT 現場主機為 macOS Mojave 10.14.5。
- 歷史 BT HMI 為 4-slot 版型；實際 slot 數與版型必須由現場畫面樣板驗證。
- 正式 BT 操作使用 JetKVM HDMI 串流取得畫面，並以 JetKVM USB HID 操作測試機。
- JetKVM 的鍵盤、絕對滑鼠與相對滑鼠必須在 Mojave 10.14.5 逐項實機驗證。

## 目標畫面流程

1. 接收並驗證 JOB 的設備、KVM IP、動作與 SN。
2. 取得最新 JetKVM 影格，使用 BT target.png 定位 HMI。
3. 若需要操作 slot，先以 slot 標籤與 checkbox／選擇器樣板讀取並複驗目前狀態，只調整與 JOB 不符的 slot。
4. 若需要輸入 SN，定位 Input_target.png，點擊輸入區、輸入 SN，並依 BT HMI 已驗證的規則送 Enter 或其他確認鍵。
5. 若需要啟動測試，定位 Button_target.png 並點擊。
6. 測試中以 Testing_target.png 判斷，回覆 action_done,testing。
7. 測試完成後，使用 Pass_target.png 與 Fail_target.png 尋找所有結果列，依由上而下的畫面順序整理；若 HMI 有可見 SN 欄，使用 OCR 與結果列的垂直位置配對。
8. 回覆 action_done,index:SN:pass|fail,...。此回覆是本 repo 的正式視覺結果。

## 需要補齊的現場資料

目前 repo 沒有 host-app/BT pattern 資料夾，也沒有完整的 BT HMI 操作定義。實作前必須蒐集下列資料：

- 各螢幕解析度、縮放比例與未聚焦／已聚焦的 BT HMI 截圖。
- 穩定的 target.png、輸入框、開始按鈕、Testing、PASS、FAIL、slot 標籤與 checkbox／選擇器樣板。
- SN 輸入順序、每筆輸入後 Enter 的實際行為、slot 是否自動跳轉，以及何時允許按下開始按鈕。
- 4-slot 的實際版型、稀疏 slot 的選取規則與結果列對應方式。
- Mojave 10.14.5 上 JetKVM 鍵盤、絕對滑鼠、相對滑鼠的實機操作紀錄。

在以上資料補齊前，不得以固定座標或推測性操作自動控制 BT HMI。

## 錯誤處理與驗收

| 情況 | 處理 |
|---|---|
| 無 HDMI 影格 | 回報 no_signal，不送 HID |
| 找不到 HMI、輸入框、按鈕或 slot 樣板 | 回報低相似度錯誤，停止後續動作 |
| slot 複驗失敗 | 不輸入 SN、不啟動測試 |
| JetKVM HID endpoint 錯誤 | 回報 HID 錯誤，檢查 USB 線與 Mojave 的裝置辨識狀態 |
| 找不到 Testing 或 PASS／FAIL 結果 | 回覆尚無可判讀畫面狀態；不以本地 Log 補結果 |

- 以現場畫面驗證單 slot、全 slot、稀疏 slot、Testing、全 PASS、全 FAIL 與混合結果。
- 驗證不同螢幕縮放、HMI 未聚焦、重連與長時間操作。
- 每次視覺或 HID 失敗均保留原圖與辨識疊圖，供調整 pattern 與門檻。

## 目前實作狀態

- 通用 run_flow 可重用於單筆 SN 輸入與按鈕點擊，run_check 可重用於 Testing／PASS／FAIL 與 OCR 結果列辨識。
- ui_app.py 已可依設備名稱載入 pattern 資料夾；傳入 BT 現在會因資料夾尚未建立而回覆 pattern folder not found (BT)。
- BT 專用的 profile、slot 狀態機、畫面樣板與現場驗證尚未實作。
