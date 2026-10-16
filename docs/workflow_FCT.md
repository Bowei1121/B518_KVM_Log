# FCT 設備工作流程

更新日期：2026-10-16（Asia/Taipei）

## 文件定位與責任邊界

本文件是 FCT 設備畫面操作與測試結果判讀的單一來源。FCT 的正式結果來源是 B518 Log Solution 的人機畫面，與 DFU、BT 相同。本 repo 透過 JetKVM 串流畫面讀取 Log Solution，並在需要時經 USB HID 操作 ATE 電腦。

測試機本地 Log、CSV、active 目錄與 archive 目錄均由另一個專案負責。本 repo 不讀取、不監聽、不解析，也不與該專案交換資料。

治具推入不由 LabVIEW 控制。FCT 改為**推入前由設備發訊號**，LabVIEW 收到訊號後先呼叫 `monitor_start`，再進入 `check`。測試由治具推入後 ATE 自行開始，因此 FCT 沒有 `start_test`。

協定細節見 [TCP JSON 介面規格 1.0](PROTOCOL.md)，現場設定見 [部署說明](DEPLOYMENT.md)。

## 呼叫順序

1. 治具推入前，設備發訊號給 LabVIEW。
2. `monitor_start`：喚起 Log Solution 並確認進入監控中。
3. `check`：長輪詢取得本輪結果。

Log Solution FCT profile 的 start 期限需涵蓋「訊號到首次寫檔」的時間，建議先設 120 秒。實際時間待現場確認（見 [PROTOCOL.md](PROTOCOL.md) 第 10 節）。

## 前置條件

- JetKVM HDMI IN 已連到 ATE 電腦畫面，USB HID 已連到 ATE 電腦。
- 上位機已建立 JetKVM WebRTC 連線，並持續取得最新影格。
- Log Solution 已開啟並位於 Dock，ATE 視窗不可放大、不可遮住畫面右側（見 [部署說明](DEPLOYMENT.md)）。
- 共用模板 `~/Documents/template/COMMON/COMMON_log_solution_dock_icon.png` 已建立。
- `~/Documents/template/FCT/FCT_window.png`、`FCT_dock_icon.png` 仍在模板目錄中，但目前 TCP 流程不使用。

## 對外 TCP 指令

- `monitor_start`：無專屬欄位。從 Dock 喚起 Log Solution，送 Cmd+Shift+M，確認進入監控中。
- `check`：讀取 Log Solution 本輪狀態與結果，`timeout_sec` 必填（0 讀一次，>0 長輪詢，最長 3600 秒）。
- `inspect`：僅供 Monitor UI 的唯讀檢視，不上鎖。LabVIEW 不得使用。
- `input`、`start_test` 回 `unsupported_command`，不建立 KVM 連線也不送 HID。`button` 已移除，送出回 `unknown_command`。

## `monitor_start` 狀態機

1. 讀取 Log Solution 狀態。Monitoring 或 Review 回 `log_solution_busy`，不送 HID。
2. 以共用 `COMMON_log_solution_dock_icon.png` 定位並點擊一次。模板缺失或 HID 錯誤回 `hid_failed`；圖示不在畫面上回 `log_solution_not_found`。
3. 點擊後 3 秒內須讀到可靠的 Log Solution 畫面，否則回 `log_solution_not_found`。讀到 Monitoring 或 Review 回 `log_solution_busy`，仍不送快捷鍵。
4. 送出 Cmd+Shift+M。10 秒內見到 Monitoring 回成功，`data` 為 `state = "monitoring"` 與 `capacity`；否則回 `monitor_not_started`。
5. 成功時，該設備的輪次判讀已視為看過 Monitoring，之後的 `check` 會取得本輪結果。

Dock 點擊在 Log Solution 已關閉時會讓 macOS 重新開啟它；開啟後為 Standby，流程可繼續。

## `check` 狀態機（Log Solution 判讀）

`check` 與 DFU、BT 使用同一條 Log Solution 顯示契約判讀路徑，站別不影響判讀。

1. 讀取 `JetKVMClient.latest_frame()` 的 BGR 影格、遞增序號、單調接收時間及來源呈現時間戳。過期、時間戳缺少或倒退、無序、裁切或無法定位的畫面一律不取用。
2. 以兩個不對稱定位點確認方向與比例，辨識 2×2 狀態標記與一至二十格結果色帶。容量由連續有效色格及其後的容量外黑格判定。第二排為 11–20 格。
3. 每個設備需先在連線中觀察到「監控中」，才會接受後續的完成畫面。
4. 完成的畫面必須是「本輪完成」標記、容量內全部為 PASS／FAIL／NOTEST／TIMEOUT（STOPPED 視為終局，原樣回傳；COMPLETING 視為等待），且兩張不同 frame sequence 的狀態與結果一致，才回 `kind: taken`。
5. 回覆的 `data` 為 `kind`、`state`、`reason`、`capacity`、`results[{slot, status}]`，不含總結果。
   - `taken`：格數必須等於設備清單的 `channels`，否則回 `capacity_mismatch`，不交付結果。
   - `paused`：待確認，立即回覆，LabVIEW 應暫停設備動作。
   - `already_taken`：本輪結果已取用過，不重做。
   - `waiting`、`unknown`：長輪詢繼續等待。`unknown` 不得當作產品 FAIL。
6. 看不到 Log Solution 時：連續 60 秒定位失敗，點一次 Dock 圖示、5 秒後重新判讀；同一請求最多 3 次，仍看不到回 `log_solution_not_visible`。
7. 本輪已見 Monitoring 後判讀到 Standby，立即回 `round_lost`（人工停止在 Log Solution 1.1 下會落入此處）。
8. 長輪詢逾時回 `status: timeout`，`data` 帶最後觀察到的狀態。
9. 判讀在工作執行緒進行，單張上限預設 200 ms；超過上限記為 `unknown`，不阻塞其他設備。

## `inspect`（僅 Monitor UI）

欄位與回覆與 `check` 相同，但不上鎖，也不改變本輪閘門狀態，且不點擊 Dock。完整畫面須連續兩次一致才回 `taken`，代表「此刻畫面上的結果可靠」，不代表已交給 LabVIEW。

## 錯誤處理與診斷

| 情況 | 處理 |
|---|---|
| 沒有影格 | `hid_failed`，不送 HID |
| Dock 圖示或 Log Solution 視窗找不到 | `log_solution_not_found`，未送快捷鍵；請人員開啟或前景化 Log Solution |
| Cmd+Shift+M 後 10 秒內未見 Monitoring | `monitor_not_started`，不開測 |
| 看不到 Log Solution 且點 Dock 3 次仍看不到 | `log_solution_not_visible`，暫停，不得判 FAIL |
| 本輪見過 Monitoring 後回到 Standby | `round_lost`，暫停，不得判 FAIL |
| HID endpoint 錯誤 | `hid_failed`，檢查 JetKVM 到 ATE 電腦的 USB 線與喚醒狀態 |
| 格數與設定通道數不同 | `capacity_mismatch`，檢查 Log Solution profile |

- `monitor_start`、`check` 的診斷資料覆寫於 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/latest/`（例如 `FCT-01`），可由主畫面的「匹配結果」檢視。診斷資料僅供追查，不影響 TCP 回覆。

## 驗收準則

- 在實機畫面連續驗證 Monitoring、全 PASS、全 FAIL、混合 PASS／FAIL、Review（待確認）、無結果列與畫面無訊號。
- 驗證 Log Solution 被遮住時的 `log_solution_not_visible` 與 Dock 恢復。
- 驗證推入前訊號到 `monitor_start` 成功的時間，確認 FCT start 期限設定。
- 任一 HID 動作失敗時，程式必須停止後續動作並輸出可追查的辨識疊圖。
