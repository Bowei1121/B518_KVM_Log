# BT 設備工作流程

更新日期：2026-09-03（Asia/Shanghai）

## 文件定位與責任邊界

本文件是 BT 設備畫面操作與測試結果判讀的單一來源。本 repo 使用 JetKVM 串流畫面定位 BT HMI、啟動 Start All，並以畫面上的 Testing／PASS／FAIL 作為正式視覺結果。

測試機本地 Log、CSV、Thread 與測試資料目錄由另一個專案負責。本 repo 不讀取、不監聽、不解析，也不將其納入結果仲裁。

## 已確認條件

- BT 現場主機為 macOS Mojave 10.14.5。
- 歷史 BT HMI 為 4-slot 版型；實際 slot 數與版型必須由現場畫面樣板驗證。
- 正式 BT 操作使用 JetKVM HDMI 串流取得畫面，並以 JetKVM USB HID 操作測試機。
- JetKVM 的鍵盤、絕對滑鼠與相對滑鼠必須在 Mojave 10.14.5 逐項實機驗證。

## 目標畫面流程

1. 接收並驗證 JOB 的設備、KVM IP、動作與 SN。
2. 取得最新 JetKVM 影格，使用 `~/Documents/template/BT/BT_window.png` 定位 HMI。
3. 收到既有 TCP `button` 指令時，先使用 `BT_dock_icon.png` 定位並點擊 Dock 上的測試程式 icon，等待 0.5 秒讓 HMI 回到最前方。
4. 重新取得影格，以 `BT_window.png` 定位 HMI，再以 `BT_start_all.png` 定位並點擊 Start All。
5. Dock icon、HMI 或 Start All 未命中，或 HID 回報錯誤時，立即停止後續操作。
6. TCP `input` 指令不支援 BT，明確回覆錯誤且不送 HID。
7. 測試中以 `BT_testing.png` 判斷，回覆 action_done,testing。
8. 測試完成後，使用 `BT_pass.png` 與 `BT_fail.png` 尋找所有結果列，依由上而下的畫面順序整理；若 HMI 有可見 SN 欄，使用 OCR 與結果列的垂直位置配對。
9. 回覆 action_done,index:SN:pass|fail,...。此回覆是本 repo 的正式視覺結果。

## 需要補齊的現場資料

目前 repo 沒有 host-app/BT pattern 資料夾，也沒有完整的 BT HMI 操作定義。實作前必須蒐集下列資料：

- 各螢幕解析度、縮放比例與未聚焦／已聚焦的 BT HMI 截圖。
- 穩定的 `BT_window.png`、`BT_dock_icon.png`、`BT_start_all.png`、Testing、PASS 與 FAIL 樣板。
- Dock 點擊後 HMI 回到前景的實機驗證。
- Mojave 10.14.5 上 JetKVM 鍵盤、絕對滑鼠、相對滑鼠的實機操作紀錄。

在以上資料補齊前，不得以固定座標或推測性操作自動控制 BT HMI。

## 錯誤處理與驗收

| 情況 | 處理 |
|---|---|
| 無 HDMI 影格 | 回報 no_signal，不送 HID |
| 找不到 Dock icon、HMI 或 Start All 樣板 | 回報低相似度錯誤，停止後續動作 |
| 收到 BT input 指令 | 回覆 BT 不支援 input，不送 HID |
| JetKVM HID endpoint 錯誤 | 回報 HID 錯誤，檢查 USB 線與 Mojave 的裝置辨識狀態 |
| 找不到 Testing 或 PASS／FAIL 結果 | 回覆尚無可判讀畫面狀態；不以本地 Log 補結果 |

- 以現場畫面驗證 Dock 前景化、Start All、Testing、全 PASS、全 FAIL 與混合結果。
- 驗證不同螢幕縮放、HMI 未聚焦、重連與長時間操作。
- 每次流程均覆寫 `~/Documents/template/_captures/match_diagnostics/BT/latest/`，保存分步相似度、命中狀態與辨識疊圖；主畫面「匹配結果」可直接檢視。

## 目前實作狀態

- run_flow 對 BT 的 `button` 會解析為 Start All；run_check 可重用於 Testing／PASS／FAIL 與 OCR 結果列辨識。
- BT 僅使用 window、testing、pass、fail、start_all、dock_icon 六種模板。
