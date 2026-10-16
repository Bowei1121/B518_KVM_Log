# DFU 設備工作流程

更新日期：2026-10-16（Asia/Taipei）

本文件是 DFU 工作流程的單一來源。JetKVM 專案只以 HDMI 串流畫面判讀並經 USB HID 操作；DFU 的正式結果來源是 B518 Log Solution 的人機畫面。本 repo 不讀取、不比對任何本機 Log，只透過畫面與快捷鍵操作 Log Solution。

協定細節見 [TCP JSON 介面規格 1.0](PROTOCOL.md)，設備設定見 [README](../README.md) 的「設備清單與通訊協定」，現場設定見 [部署說明](DEPLOYMENT.md)。

## 呼叫順序

先監控、後開測：

1. `input`：輸入本輪 SN（只勾選 checkbox 與填 SN）。
2. `monitor_start`：喚起 Log Solution 並確認進入監控中。
3. `start_test`：點 DFU 視窗的 OK，開始測試。
4. `check`：長輪詢取得本輪結果。

## 對外 TCP 指令

- `input`：輸入本輪 SN。`items` 為 `[{"slot": 1, "sn": "SN123"}, ...]`，slot 為通道 1..N，N 取自設備清單的 `channels`。不點 OK、不送 Cmd+Shift+M。
- `monitor_start`：無專屬欄位。從 Dock 喚起 Log Solution，送 Cmd+Shift+M，確認進入監控中。
- `start_test`：無專屬欄位。點 DFU 視窗的 OK 一次。Log Solution 非監控中回 `monitor_not_ready`，不送 HID。
- `check`：讀取 Log Solution 本輪狀態與結果，`timeout_sec` 必填（0 讀一次，>0 長輪詢，最長 3600 秒）。
- `inspect`：僅供 Monitor UI 的唯讀檢視，不上鎖、不消耗本輪結果。LabVIEW 不得使用。

DFU 不支援 `button`，送出回 `unknown_command`。開始測試改用 `start_test`。

通道數只來自設備清單（`~/Documents/template/devices.json` 的 `channels`）。舊的 `device_profiles.json` 與 4／7 格 profile 已淘汰，不再讀取。

## 模板

正式模板根目錄為 `~/Documents/template/`：

- `DFU/DFU_dock_icon.png`、`DFU/DFU_window.png`、`DFU/DFU_input.png`、`DFU/DFU_button.png`（OK 按鈕）
- `DFU/DFU_checkbox_checked.png`、`DFU/DFU_checkbox_unchecked.png`
- `COMMON/COMMON_log_solution_dock_icon.png`：Log Solution 的 Dock 圖示，三站共用，`monitor_start` 與 `check` 的可見性恢復使用。

checkbox 只需這兩種狀態模板，不需要每通道一張的錨點模板。結果判讀不使用任何模板，全部來自 Log Solution 的顯示契約。

## `input` 狀態機

1. 驗證 `items`：slot 為 1..N 的整數、不可重複，SN 為 1–64 字元（可含逗號與冒號）。不合法回 `invalid_argument`，不送 HID。
2. 讀取 Log Solution 狀態。待命（standby），或完成（complete）且本輪結果已由 `check` 取用，才允許輸入。監控中（monitoring）或待確認（review）回 `log_solution_busy`；完成但尚未取用也回 `log_solution_busy`；沒有影格或狀態無法讀取回 `hid_failed`。以上皆不送 HID。
3. 以 `DFU_dock_icon.png` 定位並點擊，前景化 DFU 視窗，等待 0.3 秒。找不到或 HID 錯誤回 `hid_failed`。
4. 找 DFU window，並在視窗內找出全部 checkbox（只用兩種狀態模板）。數量必須等於通道數，否則回 `hid_failed`，不輸入任何 SN。
5. 排序：依 y 分列（同列容許半個 checkbox 高的差距），同列依 x 由左到右。排序後第 1 個為通道 1，依此類推。
6. 請求的通道應為 checked，其餘為 unchecked。只點擊狀態不符的 checkbox，等待 0.3 秒後以新影格複驗；仍不符的只重試一次。第二次仍不符回 `hid_failed`，不輸入 SN。
7. 以複驗影格定位 input 欄位，點一次，依 slot 由小到大逐筆輸入 SN，每筆後按 Enter。`data.entered` 記錄已輸入的 slot。
8. HID 錯誤回 `hid_failed`，`data.entered` 列出中途已輸入的 slot。整個 `input` 不點 OK、不送 Cmd+Shift+M。

## `monitor_start`

1. 讀取 Log Solution 狀態。Monitoring 或 Review 回 `log_solution_busy`，不送 HID。
2. 以共用的 `COMMON_log_solution_dock_icon.png` 定位並點擊一次。模板缺失或 HID 錯誤回 `hid_failed`；圖示不在畫面上回 `log_solution_not_found`。
3. 點擊後 3 秒內須讀到可靠的 Log Solution 畫面，否則回 `log_solution_not_found`。讀到 Monitoring 或 Review 回 `log_solution_busy`，仍不送快捷鍵。
4. 送出 Cmd+Shift+M。10 秒內見到 Monitoring 回成功，`data` 為 `state = "monitoring"` 與 `capacity`；否則回 `monitor_not_started`。
5. 成功時，該設備的輪次判讀已視為看過 Monitoring，之後的 `check` 會取得本輪結果。

## `start_test` 狀態機

1. 讀取 Log Solution 狀態。非 Monitoring 或無法讀取回 `monitor_not_ready`，不送 HID。沒有影格回 `hid_failed`。
2. 以 `DFU_dock_icon.png` 定位並點擊，前景化 DFU 視窗，等待 0.3 秒。
3. 找 DFU window，再以 `DFU_button.png` 定位 OK，點一次。找不到或 HID 錯誤回 `hid_failed`。
4. 成功回 `data.action = "ok"`。

## `check` 狀態機

1. 讀取 `JetKVMClient.latest_frame()` 的 BGR 影格、遞增序號、單調接收時間及來源呈現時間戳。過期、時間戳缺少或倒退、無序、裁切或無法定位的畫面一律不取用。
2. 以兩個不對稱定位點確認方向與比例，辨識 2×2 狀態標記與一至二十格結果色帶。容量由連續有效色格及其後的容量外黑格判定。
3. 每個設備需先在連線中觀察到「監控中」，才會接受後續的完成畫面。
4. 完成的畫面必須是「本輪完成」標記、容量內全部為 PASS／FAIL／NOTEST／TIMEOUT（STOPPED 視為終局，原樣回傳；COMPLETING 視為等待），且兩張不同 frame sequence 的狀態與結果一致，才回 `kind: taken`。
5. 回覆的 `data` 為 `kind`、`state`、`reason`、`capacity`、`results[{slot, status}]`，不含總結果。
   - `taken`：格數必須等於通道數，否則回 `capacity_mismatch`，不交付結果。
   - `paused`：待確認，立即回覆，LabVIEW 應暫停設備動作。
   - `already_taken`：本輪結果已取用過，不重做。
   - `waiting`、`unknown`：長輪詢繼續等待。`unknown` 不得當作產品 FAIL。
6. 看不到 Log Solution 時：連續 60 秒定位失敗，點一次 Dock 圖示、5 秒後重新判讀；同一請求最多 3 次，仍看不到回 `log_solution_not_visible`。
7. 本輪已見 Monitoring 後判讀到 Standby，立即回 `round_lost`（人工停止在 Log Solution 1.1 下會落入此處）。
8. 長輪詢逾時回 `status: timeout`，`data` 帶最後觀察到的狀態。
9. 判讀在工作執行緒進行，單張上限預設 200 ms；超過上限記為 `unknown`（`inspection_timeout`），不阻塞其他設備。
10. 同一設備重複 `check` 不會重複取用。重連會清除監控 armed 狀態，必須重新看到監控中；各設備的閘門互相隔離。

## `inspect`（僅 Monitor UI）

Monitor UI 的手動檢查。欄位與回覆與 `check` 相同，但不上鎖，也不改變本輪閘門狀態，且不點擊 Dock。它讀取目前畫面，完整畫面須連續兩次一致才回 `taken`，代表「此刻畫面上的結果可靠」，不代表已交給 LabVIEW。

## 診斷與驗證

- `input`、`monitor_start`、`start_test` 與 `check` 會覆寫 `~/Documents/template/_captures/match_diagnostics/<DEVICE_ID>/latest/` 的最近一次診斷資料，可由主畫面「匹配結果」查看。診斷資料僅供追查問題，不影響 TCP 回覆。
- Round check 的可重現影格證據及逐格辨識報告由 `tools/verify_ticket16_app_frames.py` 產生至指定隔離輸出路徑。所有未知、逾時、定位失敗及格式不一致均 fail closed。
- Log Solution 1.2 建議見 [LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md](LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md)。本流程不依賴 1.2。
- 實際 JetKVM 影格的壓縮容差與現場驗收尚未完成。壓測方法見 [LOAD_TEST.md](LOAD_TEST.md)。
