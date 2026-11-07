# DFU 設備工作流程

更新日期：2026-10-19（Asia/Taipei）

本文件是 DFU 工作流程的單一來源。JetKVM 專案只以 HDMI 串流畫面判讀並經 USB HID 操作；DFU 的正式結果來源是 B518 Log Solution 的人機畫面。本 repo 不讀取、不比對任何本機 Log，只透過畫面與快捷鍵操作 Log Solution。

協定細節見 [TCP JSON 介面規格 1.0](PROTOCOL.md)，設備設定見 [README](../README.md) 的「設備清單與通訊協定」，現場設定見 [部署說明](DEPLOYMENT.md)。

## 呼叫順序

先監控、後開測：

1. `input`：輸入本輪 SN（只勾選 checkbox 與填 SN）。
2. `monitor_start`：喚起 Log Solution、確認進入監控中，並建立本輪開始請求。必填 `timeout_sec`（1–300）。
3. `start_test`：點 DFU 視窗的 OK，開始測試。
4. `check_start`（可省略、可重複）：確認 ATE 已開始，列出已開始通道。
5. `check_finish`：長輪詢取得本輪結果。只有第一次 `taken` 可做設備動作。

## 對外 TCP 指令

- `input`：輸入本輪 SN。`items` 為 `[{"slot": 1, "sn": "SN123"}, ...]`，slot 為通道 1..N，N 取自設備清單的 `channels`。不點 OK、不送 Cmd+Shift+M。
- `monitor_start`：必填 `timeout_sec`（1–300）。準備中不送快捷鍵、直接等監控中，並建立本輪開始請求；待確認回 `log_solution_review`。
- `start_test`：無專屬欄位。點 DFU 視窗的 OK 一次。Log Solution 不是監控中（含準備中）回 `monitor_not_ready`，不送 HID。
- `check_start`／`check_finish`：讀取 Log Solution 本輪狀態與結果，`timeout_sec` 必填（0 讀一次，>0 長輪詢，最長 3600 秒）。共用 `data` 含 `kind`、`state`、`reason`、`capacity`、`active`、`results`。`check_finish` 不回 `started`。
- `inspect`：僅供 Monitor UI 的唯讀檢視，不上鎖、不消耗本輪、不回 `taken`。LabVIEW 不得使用。

`check` 與 `button` 已移除，送出回 `unknown_command`。開始測試用 `start_test`。

通道數只來自設備清單（`~/Documents/template/devices.json` 的 `channels`）。舊的 `device_profiles.json` 與 4／7 格 profile 已淘汰，不再讀取。

## 模板

正式模板根目錄為 `~/Documents/template/`：

- 選用 `DFU/DFU_region.json`（`{"x":…,"y":…,"width":…,"height":…}`，螢幕像素，現場 1920×1080／100%）：存在時以此矩形作為 DFU 視窗範圍，**不再使用 `DFU_window.png`**（視窗底色會隨 ATE 模式變化）；checkbox／輸入欄位／OK 只在矩形內找，範圍外一律不比對、不點擊。不存在時，第一次 DFU 動作會自動建立全螢幕預設（0,0,1920,1080）；現場請改成實際視窗矩形以縮小範圍。
- **決策記錄（2026-11-07，使用者於 session 中明確選擇）**：缺 `DFU_region.json` 時自動建立全螢幕預設以便現場先跑；已知限制：預設全螢幕時，視窗外同圖樣的 checkbox／OK／輸入欄位可能被誤採納。完成實際視窗矩形設定後此限制消失；固定矩形要求 DFU 視窗位置保持一致。矩形小於 checkbox 模板時回 `hid_failed`。
- `DFU/DFU_dock_icon.png`、`DFU/DFU_window.png`、`DFU/DFU_input.png`、`DFU/DFU_button.png`（OK 按鈕）
- `DFU/DFU_checkbox_checked.png`、`DFU/DFU_checkbox_unchecked.png`
- `COMMON/COMMON_log_solution_dock_icon.png`：Log Solution 的 Dock 圖示，三站共用，`monitor_start` 與 `check_start`／`check_finish` 的可見性恢復使用。

checkbox 只需這兩種狀態模板，不需要每通道一張的錨點模板。結果判讀不使用任何模板，全部來自 Log Solution 的顯示契約。

## `input` 狀態機

1. 驗證 `items`：slot 為 1..N 的整數、不可重複，SN 為 1–64 字元（可含逗號與冒號）。不合法回 `invalid_argument`，不送 HID。
2. 讀取 Log Solution 狀態。待命可以輸入。一般完成或人工停止完成，且本輪已 `taken` 或已回過 `manual_verification_required`，可以輸入下一輪 SN。這兩種完成狀態若本輪尚未交付、也沒回過 `manual_verification_required`，回 `log_solution_busy`。監控中、準備中、待確認也回 `log_solution_busy`。沒有影格或狀態無法讀取回 `hid_failed`。以上皆不送 HID。
3. 以 `DFU_dock_icon.png` 定位並點擊，立刻把滑鼠移到停放點，前景化 DFU 視窗，等待 0.3 秒，再等至少 2 張移開滑鼠之後的新影格（上限 2 秒，逾時用最新影格並在診斷記 `fresh_frame_timeout`）。找不到或 HID 錯誤回 `hid_failed`。
4. 在 `DFU_region.json` 設定的搜尋矩形內（window 模板已停用）找出全部 checkbox（只用兩種狀態模板）。數量必須等於通道數，否則先移開滑鼠、等新影格、**重試比對一次**（此階段尚未送出任何 HID，不會重複點擊或輸入；不再點 Dock，診斷 key 加 `_retry`）；兩次都失敗回 `hid_failed`，不輸入任何 SN。
5. 排序：依 y 分列（同列容許半個 checkbox 高的差距），同列依 x 由左到右。排序後第 1 個為通道 1，依此類推。
6. 請求的通道應為 checked，其餘為 unchecked。只點擊狀態不符的 checkbox，等待 0.3 秒後以新影格複驗；仍不符的只重試一次。第二次仍不符回 `hid_failed`，不輸入 SN。
7. 以複驗影格定位 input 欄位，點一次，依 slot 由小到大逐筆輸入 SN，每筆後按 Enter。`data.entered` 記錄已輸入的 slot。
8. HID 錯誤回 `hid_failed`，`data.entered` 列出中途已輸入的 slot。整個 `input` 不點 OK、不送 Cmd+Shift+M。

## `monitor_start`

1. `timeout_sec` 必填，1–300，否則 `invalid_argument`，不送 HID。這就是等候監控中的時間。
2. 已在監控中回 `log_solution_busy`，不建立新的本輪開始請求。待確認回 `log_solution_review`，不送 HID。
3. 準備中不點 Dock、不送快捷鍵，直接等監控中，並建立（或保留）本輪開始請求。
4. 其餘狀態以共用的 `COMMON_log_solution_dock_icon.png` 點一次。模板缺失或 HID 錯誤回 `hid_failed`；圖示不在畫面上回 `log_solution_not_found`。
5. 點擊後 3 秒內須讀到可靠畫面，否則 `log_solution_not_found`。監控中回 `log_solution_busy`；待確認回 `log_solution_review`；準備中改為等待、不送快捷鍵。待命、一般完成、人工停止完成才送 Cmd+Shift+M，並建立本輪開始請求。
6. `timeout_sec` 內見到監控中即成功，`data` 為 `state = "monitoring"` 與 `capacity`，且這一次呼叫算「已見本次監控就緒」。逾時回 `monitor_not_started`，不清除本輪開始請求。只有之後的 `taken` 或 `manual_verification_required` 才清除。

## `start_test` 狀態機

1. 讀取 Log Solution 狀態。不是監控中（含準備中、看不清楚或沒有影格）回 `monitor_not_ready`，不送 HID。
2. 以 `DFU_dock_icon.png` 定位並點擊，立刻把滑鼠移到停放點，前景化 DFU 視窗，等待 0.3 秒，再等至少 2 張移開滑鼠之後的新影格（上限 2 秒）。
3. 在 `DFU_region.json` 設定的搜尋矩形內以 `DFU_button.png` 定位 OK。未命中時不再點 Dock，移開滑鼠、等新影格後重試比對一次（`_retry`）；兩次都失敗回 `hid_failed`。命中後點一次，點完把滑鼠移回停放點。HID 錯誤回 `hid_failed`。
4. 成功回 `data.action = "ok"`。

停放位置與等待上限設定：`~/Documents/template/COMMON/hid_settings.json`（選用）：`{"pointer_park": [x, y], "fresh_frame_timeout_sec": 2.0}`。`pointer_park` 為點擊後滑鼠停放的螢幕像素位置（預設螢幕上緣中間 `(寬/2, 1)`，超出畫面會夾回邊界）；現場若開啟「自動隱藏選單列」，上緣會讓選單列跑出來，請改成不會觸發選單列／Dock／熱角的空白處，例如 `[960, 600]`。`fresh_frame_timeout_sec` 為等新影格上限（預設 2 秒）。格式錯誤回 `hid_failed`。

## `check_start`／`check_finish`

判讀規則、六種圖樣、`kind` 與錯誤碼以 [PROTOCOL.md](PROTOCOL.md) 第 5.4 節為準。DFU 與此相同的要點：

1. 沒有本輪開始請求回 `monitor_not_ready`，不取用畫面殘留結果。
2. 有請求、未見本次監控中、畫面已可交付（一般完成或人工停止完成）回 `manual_verification_required`，不交付。
3. 有請求且已見本次監控中：兩張相同簽章（完成種類＋格數＋每格結果）才 `taken`。`check_start` 也可在活動通道上回 `started`（`active[].evidence` 為 `testing` 或 `result`），重複呼叫不消耗本輪。
4. 同一簽章再查回 `already_taken`，`results` 與第一次相同。看到準備中、監控中，或新的 `monitor_start`，就不再是同一輪。
5. 待確認回 `paused`。看不清楚回 `unknown`，並套用可見性恢復：連續 30 秒看不到才點 Dock，等 5 秒，同一請求最多 3 次。準備中是可靠讀數，不點 Dock。
6. 格數不等於通道數回 `capacity_mismatch`。本輪已見監控中後回到待命回 `round_lost`。
7. 長輪詢逾時回 `status: timeout`。判讀在工作執行緒，單張上限預設 200 ms。

## `inspect`（僅 Monitor UI）

欄位與 `check_start` 相同，但不上鎖、不點 Dock、不消耗本輪、不回 `taken`。兩張新鮮影格確認活動通道時回 `started`，畫面顯示中文狀態與「測試中／已有結果」。

## 診斷與驗證

- `input`、`monitor_start`、`start_test` 與 `check_finish` 會覆寫 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/latest/` 的最近一次診斷資料，可由主畫面「匹配結果」查看。診斷資料僅供追查問題，不影響 TCP 回覆。
- **歷史歸檔（診斷圖保留 24 小時）**：每次動作 `finalize()` 時，另存一份到 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/history/<YYYYMMDD>/<HHMMSS_mmm>_<operation>_<OK|FAIL>/`（本地時間）。內含 `summary.json` 與標註圖；**history 只存 JPEG**（品質 85，`summary.json` 的 `image` 欄位指向 `.jpg`），`latest/` 仍為 PNG。每次歸檔後自動清掃所有裝置的 `history/`：超過 24 小時（`HISTORY_HOURS`）的資料夾刪除；總大小超過 2048 MB（`HISTORY_MAX_MB`）時從最舊的刪起；空的日期資料夾一併刪除。兩個值為 `host-app/match_diagnostics.py` 的模組常數，也可由 `MatchDiagnostics(..., history_hours=, history_max_mb=)` 覆寫。歸檔與清理在背景單一 daemon 執行緒進行（不卡 Core 的 TCP event loop，也不拖慢 Core 結束；先寫暫存再整份改名發布（結束或中斷不會留下半份歸檔，超過 1 小時沒動的殘留暫存於下次清理時刪除；其他程序正在寫的暫存不會被動（以 mtime 判斷：單次歸檔停滯超過 1 小時的暫存會被視為殘留而刪除，該次歸檔即遺失）；多個程序同時發布時，日期資料夾被清掉或同名已被佔用會自動重試並改用下一個後綴）；待處理工作最多 8 件／128 MB，滿了就略過該次歸檔並記 log，`latest/` 不受影響），同毫秒同動作同結果的目錄會加 `_2`、`_3` 後綴而不覆蓋；任何 IO 錯誤只記 log，不影響流程與 TCP 回覆。
- `monitor_start` 點 Dock 後若在確認時間內讀不到可靠的 Log Solution，會在診斷中多一筆 `confirm_last_unreliable`（最後一張判讀不可靠的影格與最後判讀原因）。
- Round check 的可重現影格證據及逐格辨識報告由 `tools/verify_ticket16_app_frames.py` 產生至指定隔離輸出路徑。所有未知、逾時、定位失敗及格式不一致均 fail closed。
- 六種整輪圖樣（含準備中、人工停止完成）的判讀已實作。Log Solution 應用程式本身的圖樣與幾何仍見 [LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md](LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md)。左定位點移動不在本次範圍。
- 實際 JetKVM 影格的壓縮容差與現場驗收尚未完成。壓測方法見 [LOAD_TEST.md](LOAD_TEST.md)。
