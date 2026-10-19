# BT 設備工作流程

更新日期：2026-10-19（Asia/Taipei）

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

1. `monitor_start`：喚起 Log Solution、確認進入監控中，並建立本輪開始請求。必填 `timeout_sec`（1–300）。
2. `start_test`：點 BT 視窗的 Start All，開始測試。
3. `check_start`（可省略、可重複）：確認 ATE 已開始。
4. `check_finish`：長輪詢取得本輪結果。只有第一次 `taken` 可做設備動作。

## 對外 TCP 指令

- `monitor_start`：必填 `timeout_sec`（1–300）。準備中不送快捷鍵並建立本輪開始請求；待確認回 `log_solution_review`。
- `start_test`：無專屬欄位。BT 點 Start All 一次。Log Solution 不是監控中（含準備中）回 `monitor_not_ready`，不送 HID。
- `check_start`／`check_finish`：`timeout_sec` 必填（0–3600）。判讀與 DFU、FCT 相同，見 [PROTOCOL.md](PROTOCOL.md) 第 5.4 節。
- `inspect`：僅供 Monitor UI，不上鎖、不回 `taken`。LabVIEW 不得使用。
- `input` 回 `unsupported_command`，不送 HID。`check` 與 `button` 已移除，送出回 `unknown_command`。

## 模板

- `BT/BT_dock_icon.png`：BT 程式在 Dock 上的 icon，`start_test` 前景化使用。
- `BT/BT_window.png`：BT 視窗定位。
- `BT/BT_start_all.png`：Start All 按鈕。
- `COMMON/COMMON_log_solution_dock_icon.png`：Log Solution 的 Dock 圖示，三站共用，`monitor_start` 與 `check_start`／`check_finish` 的可見性恢復使用。

BT 不再使用 `testing`、`pass`、`fail` 模板；結果判讀全部來自 Log Solution 的顯示契約。

## `monitor_start` 狀態機

與 DFU 相同，見 [workflow_DFU.md](workflow_DFU.md)。要點：準備中不送快捷鍵並建立本輪開始請求；待確認回 `log_solution_review`；已在監控中回 `log_solution_busy`；`timeout_sec` 內見到監控中才成功；`monitor_not_started` 不清除請求。

## `start_test` 狀態機

1. 讀取 Log Solution 狀態。不是監控中（含準備中、看不清楚或沒有影格）回 `monitor_not_ready`，不送 HID。
2. 以 `BT_dock_icon.png` 定位並點擊，等待 0.5 秒讓 BT 視窗回到最前方。Dock 未命中或 HID 錯誤回 `hid_failed`。
3. 重新取得影格，以 `BT_window.png` 定位視窗，再以 `BT_start_all.png` 定位並點擊一次 Start All。視窗或按鈕未命中、HID 錯誤回 `hid_failed`。
4. 成功回 `data.action = "start_all"`。

## `check_start`／`check_finish`

與 DFU、FCT 同一條判讀路徑。規則見 [PROTOCOL.md](PROTOCOL.md) 第 5.4 節：沒有本輪開始請求回 `monitor_not_ready`；未見本次監控中的可交付畫面回 `manual_verification_required`；兩張相同簽章才 `taken`；同一輪再查為 `already_taken` 並附相同結果；`check_start` 的活動通道回 `started` 且不消耗本輪。看不到 Log Solution 連續 30 秒才點 Dock，等 5 秒，最多 3 次。準備中是可靠讀數。本輪已見監控中後回到待命回 `round_lost`。

## `inspect`（僅 Monitor UI）

不上鎖、不點 Dock、不回 `taken`。兩張新鮮影格確認活動通道時回 `started`。

## 錯誤處理與驗收

| 情況 | 處理 |
| --- | --- |
| 無 HDMI 影格 | `monitor_start` 回 `hid_failed`；`start_test` 回 `monitor_not_ready`；皆不送 HID |
| Log Solution 未在監控中就要求開測 | `monitor_not_ready`，不送 HID |
| Dock 圖示、Log Solution 視窗找不到 | `log_solution_not_found`，未送快捷鍵 |
| `timeout_sec` 內未見監控中 | `monitor_not_started`，不清除本輪開始請求 |
| 找不到 BT 視窗或 Start All | `hid_failed`，停止後續動作 |
| 看不到 Log Solution 且點 Dock 3 次仍看不到 | `log_solution_not_visible`，暫停，不得判 FAIL |
| 本輪見過 Monitoring 後回到 Standby | `round_lost`，暫停，不得判 FAIL |
| JetKVM HID endpoint 錯誤 | `hid_failed`，檢查 USB 線與 Mojave 的裝置辨識狀態 |

- 現場驗收：Dock 前景化、Start All、Monitoring、全 PASS、全 FAIL 與混合結果、Review（待確認）。
- 驗證不同螢幕縮放、Log Solution 未聚焦、重連與長時間操作。
- `monitor_start`、`start_test` 與 `check_finish` 的診斷資料覆寫於 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/latest/`（例如 `BT-01`），保存分步相似度、命中狀態與辨識疊圖；主畫面「匹配結果」可直接檢視。

## 現場待辦

- 在 Mojave 10.14.5 上確認 JetKVM 鍵盤、絕對滑鼠與相對滑鼠的實機操作紀錄。
- 確認 Dock 點擊後 BT 視窗回到前景，並以現場畫面建立 `BT_dock_icon.png`、`BT_window.png`、`BT_start_all.png`。
