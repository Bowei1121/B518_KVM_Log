# FCT 設備工作流程

更新日期：2026-10-19（Asia/Taipei）

## 文件定位與責任邊界

本文件是 FCT 設備畫面操作與測試結果判讀的單一來源。FCT 的正式結果來源是 B518 Log Solution 的人機畫面，與 DFU、BT 相同。本 repo 透過 JetKVM 串流畫面讀取 Log Solution，並在需要時經 USB HID 操作 ATE 電腦。

測試機本地 Log、CSV、active 目錄與 archive 目錄均由另一個專案負責。本 repo 不讀取、不監聽、不解析，也不與該專案交換資料。

治具推入不由 LabVIEW 控制。FCT 改為**推入前由設備發訊號**，LabVIEW 收到訊號後先呼叫 `monitor_start`，再以 `check_start`（可省略）與 `check_finish` 取得結果。測試由治具推入後 ATE 自行開始，因此 FCT 沒有 `start_test`。

協定細節見 [TCP JSON 介面規格 1.0](PROTOCOL.md)，現場設定見 [部署說明](DEPLOYMENT.md)。

## 呼叫順序

1. 治具推入前，設備發訊號給 LabVIEW。
2. `monitor_start`：喚起 Log Solution、確認進入監控中，並建立本輪開始請求。必填 `timeout_sec`（1–300）。
3. `check_start`（可省略、可重複）：確認 ATE 已開始。
4. `check_finish`：長輪詢取得本輪結果。只有第一次 `taken` 可做設備動作。

Log Solution FCT profile 的 start 期限需涵蓋「訊號到首次寫檔」的時間，建議先設 120 秒。實際時間待現場確認（見 [PROTOCOL.md](PROTOCOL.md) 第 10 節）。

## 前置條件

- JetKVM HDMI IN 已連到 ATE 電腦畫面，USB HID 已連到 ATE 電腦。
- 上位機已建立 JetKVM WebRTC 連線，並持續取得最新影格。
- Log Solution 已開啟並位於 Dock，ATE 視窗不可放大、不可遮住畫面右側（見 [部署說明](DEPLOYMENT.md)）。
- 共用模板 `~/Documents/template/COMMON/COMMON_log_solution_dock_icon.png` 已建立。
- `~/Documents/template/FCT/FCT_window.png`、`FCT_dock_icon.png` 仍在模板目錄中，但目前 TCP 流程不使用。

## 對外 TCP 指令

- `monitor_start`：必填 `timeout_sec`（1–300）。準備中不送快捷鍵並建立本輪開始請求；待確認回 `log_solution_review`。
- `check_start`／`check_finish`：`timeout_sec` 必填（0–3600）。判讀與 DFU、BT 相同，見 [PROTOCOL.md](PROTOCOL.md) 第 5.4 節。
- `inspect`：僅供 Monitor UI，不上鎖、不回 `taken`。LabVIEW 不得使用。
- `input`、`start_test` 回 `unsupported_command`，不建立 KVM 連線也不送 HID。`check` 與 `button` 已移除，送出回 `unknown_command`。

## `monitor_start` 狀態機

與 DFU 相同，見 [workflow_DFU.md](workflow_DFU.md)。要點：準備中不送快捷鍵並建立本輪開始請求；待確認回 `log_solution_review`；已在監控中回 `log_solution_busy`；`timeout_sec` 內見到監控中才成功；`monitor_not_started` 不清除請求。Dock 點擊在 Log Solution 已關閉時會讓 macOS 重新開啟它；開啟後為 Standby，流程可繼續。

## `check_start`／`check_finish`

與 DFU、BT 同一條判讀路徑，站別不影響判讀。規則見 [PROTOCOL.md](PROTOCOL.md) 第 5.4 節：沒有本輪開始請求回 `monitor_not_ready`；未見本次監控中的可交付畫面回 `manual_verification_required`；兩張相同簽章才 `taken`；同一輪再查為 `already_taken` 並附相同結果；`check_start` 的活動通道回 `started` 且不消耗本輪。看不到 Log Solution 連續 30 秒才點 Dock，等 5 秒，最多 3 次。準備中是可靠讀數。本輪已見監控中後回到待命回 `round_lost`。

## `inspect`（僅 Monitor UI）

不上鎖、不點 Dock、不回 `taken`。兩張新鮮影格確認活動通道時回 `started`。

## 錯誤處理與診斷

| 情況 | 處理 |
|---|---|
| 沒有影格 | `hid_failed`，不送 HID |
| Dock 圖示或 Log Solution 視窗找不到 | `log_solution_not_found`，未送快捷鍵；請人員開啟或前景化 Log Solution |
| `timeout_sec` 內未見監控中 | `monitor_not_started`，不清除本輪開始請求 |
| 看不到 Log Solution 且點 Dock 3 次仍看不到 | `log_solution_not_visible`，暫停，不得判 FAIL |
| 本輪見過 Monitoring 後回到 Standby | `round_lost`，暫停，不得判 FAIL |
| HID endpoint 錯誤 | `hid_failed`，檢查 JetKVM 到 ATE 電腦的 USB 線與喚醒狀態 |
| 格數與設定通道數不同 | `capacity_mismatch`，檢查 Log Solution profile |

- `monitor_start`、`check_finish` 的診斷資料覆寫於 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/latest/`（例如 `FCT-01`），可由主畫面的「匹配結果」檢視。診斷資料僅供追查，不影響 TCP 回覆。

## 驗收準則

- 在實機畫面連續驗證 Monitoring、全 PASS、全 FAIL、混合 PASS／FAIL、Review（待確認）、無結果列與畫面無訊號。
- 驗證 Log Solution 被遮住時的 `log_solution_not_visible` 與 Dock 恢復。
- 驗證推入前訊號到 `monitor_start` 成功的時間，確認 FCT start 期限設定。
- 任一 HID 動作失敗時，程式必須停止後續動作並輸出可追查的辨識疊圖。
