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
2. 以 `BT_dock_icon.png` 定位並點擊，**立刻把滑鼠移到停放點**（避免 Dock 的程式名稱提示蓋住 Start All），等待 0.5 秒讓 BT 視窗回到最前方。Dock 未命中或 HID 錯誤回 `hid_failed`。
3. **等新影格**：等到至少 2 張在移開滑鼠之後才收到的影格（上限 2 秒；逾時就用最新影格繼續，診斷多一筆 `fresh_frame_timeout`），以最後一張辨識，不用點擊前的舊影格。
4. 以 `BT_window.png` 定位視窗，再以 `BT_start_all.png` 定位。任一未命中時**不再點 Dock**：再移開滑鼠一次、再等新影格、重新比對一次（診斷 key 加 `_retry`）。兩次都失敗回 `hid_failed`，訊息帶兩次中較高的相似度。
5. 命中後點擊一次 Start All，點完把滑鼠移回停放點，避免 hover／提示影響之後 `check_*` 讀 Log Solution。HID 錯誤回 `hid_failed`。
6. 成功回 `data.action = "start_all"`。

停放位置與等待上限設定：`~/Documents/template/COMMON/hid_settings.json`（選用）：`{"pointer_park": [x, y], "fresh_frame_timeout_sec": 2.0}`。`pointer_park` 為點擊後滑鼠停放的螢幕像素位置（預設螢幕上緣中間 `(寬/2, 1)`，超出畫面會夾回邊界）；現場若開啟「自動隱藏選單列」，上緣會讓選單列跑出來，請改成不會觸發選單列／Dock／熱角的空白處，例如 `[960, 600]`。`fresh_frame_timeout_sec` 為等新影格上限（預設 2 秒）。格式錯誤回 `hid_failed`。

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
| 找不到 BT 視窗或 Start All（已用新影格重試一次） | `hid_failed`，停止後續動作，Dock 只點過一次 |
| 看不到 Log Solution 且點 Dock 3 次仍看不到 | `log_solution_not_visible`，暫停，不得判 FAIL |
| 本輪見過 Monitoring 後回到 Standby | `round_lost`，暫停，不得判 FAIL |
| JetKVM HID endpoint 錯誤 | `hid_failed`，檢查 USB 線與 Mojave 的裝置辨識狀態 |

- 現場驗收：Dock 前景化、Start All、Monitoring、全 PASS、全 FAIL 與混合結果、Review（待確認）。
- 驗證不同螢幕縮放、Log Solution 未聚焦、重連與長時間操作。
- `monitor_start`、`start_test` 與 `check_finish` 的診斷資料覆寫於 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/latest/`（例如 `BT-01`），保存分步相似度、命中狀態與辨識疊圖；主畫面「匹配結果」可直接檢視。
- **歷史歸檔（診斷圖保留 24 小時）**：每次動作 `finalize()` 時，另存一份到 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/history/<YYYYMMDD>/<HHMMSS_mmm>_<operation>_<OK|FAIL>/`（本地時間）。內含 `summary.json` 與標註圖；**history 只存 JPEG**（品質 85，`summary.json` 的 `image` 欄位指向 `.jpg`），`latest/` 仍為 PNG。每次歸檔後自動清掃所有裝置的 `history/`：超過 24 小時（`HISTORY_HOURS`）的資料夾刪除；總大小超過 2048 MB（`HISTORY_MAX_MB`）時從最舊的刪起；空的日期資料夾一併刪除。兩個值為 `host-app/match_diagnostics.py` 的模組常數，也可由 `MatchDiagnostics(..., history_hours=, history_max_mb=)` 覆寫。歸檔與清理在背景單一 daemon 執行緒進行（不卡 Core 的 TCP event loop，也不拖慢 Core 結束；先寫暫存再整份改名發布（結束或中斷不會留下半份歸檔，超過 1 小時沒動的殘留暫存於下次清理時刪除；其他程序正在寫的暫存不會被動（以 mtime 判斷：單次歸檔停滯超過 1 小時的暫存會被視為殘留而刪除，該次歸檔即遺失）；多個程序同時發布時，日期資料夾被清掉或同名已被佔用會自動重試並改用下一個後綴）；待處理工作最多 8 件／128 MB，滿了就略過該次歸檔並記 log，`latest/` 不受影響），同毫秒同動作同結果的目錄會加 `_2`、`_3` 後綴而不覆蓋；任何 IO 錯誤只記 log，不影響流程與 TCP 回覆。
- `monitor_start` 點 Dock 後若在確認時間內讀不到可靠的 Log Solution，會在診斷中多一筆 `confirm_last_unreliable`（最後一張判讀不可靠的影格與最後判讀原因）。

## 現場待辦

- 在 Mojave 10.14.5 上確認 JetKVM 鍵盤、絕對滑鼠與相對滑鼠的實機操作紀錄。
- Dock 提示蓋住 Start All 的排錯：看診斷圖有無 `*_retry` 與 `fresh_frame_timeout`；確認 `hid_settings.json` 的 `pointer_park` 不在 Dock 或選單列上。
- 確認 Dock 點擊後 BT 視窗回到前景，並以現場畫面建立 `BT_dock_icon.png`、`BT_window.png`、`BT_start_all.png`。
