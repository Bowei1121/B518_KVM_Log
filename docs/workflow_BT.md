# BT 設備工作流程

更新日期：2026-10-16（Asia/Taipei）

## 文件定位與責任邊界

本文件是 BT 設備畫面操作與測試結果判讀的單一來源。BT 的正式結果來源是 B518 Log Solution 的人機畫面，與 DFU、FCT 相同。本 repo 使用 JetKVM 串流畫面喚起 Log Solution、啟動 BT 的 Start All，並讀取 Log Solution 的標記與色帶判讀結果。

測試機本地 Log、CSV、Thread 與測試資料目錄由另一個專案負責。本 repo 不讀取、不監聽、不解析，也不將其納入結果仲裁。

協定細節見 [TCP JSON 介面規格 1.0](PROTOCOL.md)，現場設定見 [部署說明](DEPLOYMENT.md)。

## 已確認條件

- BT 現場主機為 macOS Mojave 10.14.5。
- 正式 BT 操作使用 JetKVM HDMI 串流取得畫面，並以 JetKVM USB HID 操作測試機。
- JetKVM 的鍵盤、絕對滑鼠與相對滑鼠必須在 Mojave 10.14.5 逐項實機驗證。

## 呼叫順序

先監控、後開測：

1. `monitor_start`：喚起 Log Solution 並確認進入監控中。
2. `start_test`：點 BT 視窗的 Start All，開始測試。
3. `check`：長輪詢取得本輪結果。

## 對外 TCP 指令

- `monitor_start`：無專屬欄位。從 Dock 喚起 Log Solution，送 Cmd+Shift+M，確認進入監控中。
- `start_test`：無專屬欄位。BT 點 Start All 一次。Log Solution 非監控中回 `monitor_not_ready`，不送 HID。
- `check`：讀取 Log Solution 本輪狀態與結果，`timeout_sec` 必填（0 讀一次，>0 長輪詢，最長 3600 秒）。
- `inspect`：僅供 Monitor UI 的唯讀檢視，不上鎖。LabVIEW 不得使用。
- `input` 回 `unsupported_command`，不送 HID。`button` 已移除，送出回 `unknown_command`。

## 模板

- `BT/BT_dock_icon.png`：BT 程式在 Dock 上的 icon，`start_test` 前景化使用。
- `BT/BT_window.png`：BT 視窗定位。
- `BT/BT_start_all.png`：Start All 按鈕。
- `COMMON/COMMON_log_solution_dock_icon.png`：Log Solution 的 Dock 圖示，三站共用，`monitor_start` 與 `check` 的可見性恢復使用。

BT 不再使用 `testing`、`pass`、`fail` 模板；結果判讀全部來自 Log Solution 的顯示契約。

## `monitor_start` 狀態機

1. 讀取 Log Solution 狀態。Monitoring 或 Review 回 `log_solution_busy`，不送 HID。
2. 以共用 `COMMON_log_solution_dock_icon.png` 定位並點擊一次。模板缺失或 HID 錯誤回 `hid_failed`；圖示不在畫面上回 `log_solution_not_found`。
3. 點擊後 3 秒內須讀到可靠的 Log Solution 畫面，否則回 `log_solution_not_found`。讀到 Monitoring 或 Review 回 `log_solution_busy`，仍不送快捷鍵。
4. 送出 Cmd+Shift+M。10 秒內見到 Monitoring 回成功，`data` 為 `state = "monitoring"` 與 `capacity`；否則回 `monitor_not_started`。
5. 成功時，該設備的輪次判讀已視為看過 Monitoring，之後的 `check` 會取得本輪結果。

## `start_test` 狀態機

1. 讀取 Log Solution 狀態。非 Monitoring 或無法讀取回 `monitor_not_ready`，不送 HID。沒有影格回 `hid_failed`。
2. 以 `BT_dock_icon.png` 定位並點擊，等待 0.5 秒讓 BT 視窗回到最前方。Dock 未命中或 HID 錯誤回 `hid_failed`。
3. 重新取得影格，以 `BT_window.png` 定位視窗，再以 `BT_start_all.png` 定位並點擊一次 Start All。視窗或按鈕未命中、HID 錯誤回 `hid_failed`。
4. 成功回 `data.action = "start_all"`。

## `check` 狀態機（Log Solution 判讀）

`check` 與 DFU、FCT 使用同一條 Log Solution 顯示契約判讀路徑，站別不影響判讀。

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

## 錯誤處理與驗收

| 情況 | 處理 |
| --- | --- |
| 無 HDMI 影格 | `hid_failed`，不送 HID |
| Log Solution 未在監控中就要求開測 | `monitor_not_ready`，不送 HID |
| Dock 圖示、Log Solution 視窗找不到 | `log_solution_not_found`，未送快捷鍵 |
| Cmd+Shift+M 後 10 秒內未見 Monitoring | `monitor_not_started`，不開測 |
| 找不到 BT 視窗或 Start All | `hid_failed`，停止後續動作 |
| 看不到 Log Solution 且點 Dock 3 次仍看不到 | `log_solution_not_visible`，暫停，不得判 FAIL |
| 本輪見過 Monitoring 後回到 Standby | `round_lost`，暫停，不得判 FAIL |
| JetKVM HID endpoint 錯誤 | `hid_failed`，檢查 USB 線與 Mojave 的裝置辨識狀態 |

- 現場驗收：Dock 前景化、Start All、Monitoring、全 PASS、全 FAIL 與混合結果、Review（待確認）。
- 驗證不同螢幕縮放、Log Solution 未聚焦、重連與長時間操作。
- `monitor_start`、`start_test` 與 `check` 的診斷資料覆寫於 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/latest/`（例如 `BT-01`），保存分步相似度、命中狀態與辨識疊圖；主畫面「匹配結果」可直接檢視。

## 現場待辦

- 在 Mojave 10.14.5 上確認 JetKVM 鍵盤、絕對滑鼠與相對滑鼠的實機操作紀錄。
- 確認 Dock 點擊後 BT 視窗回到前景，並以現場畫面建立 `BT_dock_icon.png`、`BT_window.png`、`BT_start_all.png`。
