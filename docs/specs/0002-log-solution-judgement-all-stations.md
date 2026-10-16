# Spec: 三站統一以 Log Solution 判讀、監控與開測指令

GitHub issue：https://github.com/Bowei1121/B518_KVM_Log/issues/21（本檔為發布時的本機副本）

相關文件：`docs/PROJECT_SUMMARY_20261016.md`（grilling 第 1～5 輪與共同理解）、`docs/PROTOCOL.md`（協定 1.0）、`docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md`、`CONTEXT.md`、`docs/LABVIEW_LOG_SOLUTION_INTEGRATION.md`、spec #1（`docs/specs/0001-device-identity-protocol-1.0.md`）。保留 ADR-0002（fail-fast）、ADR-0003（JSON 不向下相容）、ADR-0005（Frame Freeze Guard）、ADR-0006（每設備一條連線）、ADR-0007（設備清單與統一設備 ID）。

## Problem Statement

LabVIEW 測試流程控制者要讓 DFU、FCT、BT 三站都以 Log Solution 正式結果來源取得本輪結果（整合規格已確認），但目前：

- 只有 DFU 的 `check` 讀 Log Solution 顯示契約；FCT／BT 仍用模板判讀（灰階、單一門檻、單張畫面、同格可能重複計數、沒有穩定確認與 already_taken），可靠度低。
- 沒有「開始監控」的獨立指令。DFU 的 `input` 在點 OK（點下即開始測試）之後才送 Cmd+Shift+M，順序與「先監控、確認監控中、再開測」相反；FCT／BT 完全沒有開始監控的方法。
- BT 開測用 `button`，DFU 開測藏在 `input` 裡，FCT 由治具推入自動開測；三站沒有一致的呼叫順序。
- Log Solution 沒有常駐最上層，監控中可能被 ATE 視窗遮住；橋接看不到色帶只會一直等到逾時，LabVIEW 無法及早通知人員。
- Log Solution 當掉重開或人工停止（1.1 下顯示 standby）時，本輪結果已無法取得，但橋接仍回「等待中」，LabVIEW 只能乾等。

## Solution

三站的 `check` 全部走同一條 Log Solution 顯示契約判讀路徑，沿用 DFU 已驗證的防呆，淘汰模板判讀結果。新增三站共用的 `monitor_start`（從 Dock 喚起 Log Solution、送快捷鍵、確認進入監控中）與 `start_test`（DFU 點 OK、BT 點 Start All）；DFU `input` 只填 SN。`check` 在看不到 Log Solution 時會主動點 Dock 圖示嘗試恢復，仍看不到即回錯誤；監控中途變回 standby 立即回 `round_lost`。LabVIEW 三站各自依固定順序呼叫，先監控、後開測。

## User Stories

判讀

1. As a LabVIEW 測試流程控制者, I want FCT and BT `check` to read the Log Solution result band exactly like DFU, so that all stations deliver results from the 正式結果來源.
2. As a LabVIEW 測試流程控制者, I want `check` on every station to require Monitoring to have been seen on this stream before accepting a Complete screen, so that a leftover Complete from a previous round is never taken.
3. As a LabVIEW 測試流程控制者, I want every station to require two fresh, identical, complete frames before `taken`, so that a half-drawn band is never delivered.
4. As a LabVIEW 測試流程控制者, I want `capacity_mismatch` on every station when the band's slot count differs from the device's 測試通道數, so that a mis-configured Log Solution profile never delivers results.
5. As a LabVIEW 測試流程控制者, I want `already_taken` on every station after a round was delivered, so that I never act twice on the same round.
6. As a LabVIEW 測試流程控制者, I want `paused` returned immediately on every station when Log Solution shows Review, so that I stop downstream equipment actions at once.
7. As a LabVIEW 測試流程控制者, I want results of up to 20 channels in two rows (channels 11–N on the second row) to be read on every station, so that other projects (e.g. 20-, 7- and 6-channel equipment) work by configuration only.
8. As a LabVIEW 測試流程控制者, I want an ATE where Log Solution is not installed yet to return `unknown` rather than any product result, so that a station that has not migrated can never be misjudged.

開始監控

9. As a LabVIEW 測試流程控制者, I want a `monitor_start` command on all three stations, so that I start Log Solution monitoring the same way everywhere.
10. As a LabVIEW 測試流程控制者, I want `monitor_start` to click the Log Solution Dock icon and confirm the Log Solution window is on screen before sending Cmd+Shift+M, so that the shortcut is only sent when Log Solution is running and visible.
11. As a LabVIEW 測試流程控制者, I want `log_solution_not_found` when the Dock icon or the Log Solution window cannot be found, so that I can ask an operator to open Log Solution.
12. As a LabVIEW 測試流程控制者, I want `monitor_start` to succeed only after the marker shows Monitoring, within 10 seconds of the shortcut, so that I start the test only when monitoring is confirmed.
13. As a LabVIEW 測試流程控制者, I want `monitor_not_started` when Monitoring is not seen within 10 seconds, so that I never start a test without monitoring.
14. As a LabVIEW 測試流程控制者, I want `log_solution_busy` without sending the shortcut when Log Solution is already Monitoring or in Review, so that a running round is never disturbed.
15. As a LabVIEW 測試流程控制者, I want `monitor_start` to be allowed from Standby or Complete, so that I can start the next round right after taking the previous one.

開始測試

16. As a LabVIEW 測試流程控制者, I want a `start_test` command that clicks OK on DFU and Start All on BT, so that I start tests with one command name for both stations.
17. As a LabVIEW 測試流程控制者, I want `start_test` on FCT to return `unsupported_command`, so that it is explicit that FCT starts by itself when the fixture is pushed in.
18. As a LabVIEW 測試流程控制者, I want `start_test` to return `monitor_not_ready` unless Log Solution is Monitoring, so that a test is never started before monitoring.
19. As a LabVIEW 測試流程控制者, I want `start_test` to report a HID or locate failure as `hid_failed`, so that I treat it as an equipment fault.
20. As a LabVIEW 測試流程控制者, I want the old `button` command removed, so that there is exactly one way to start a test.

DFU 輸入

21. As a LabVIEW 測試流程控制者, I want DFU `input` to only set checkboxes and type SNs, without clicking OK or sending the shortcut, so that I control when monitoring and the test start.
22. As a LabVIEW 測試流程控制者, I want DFU `input` to keep rejecting with `log_solution_busy` while Monitoring or Review, so that next-round SNs never enter a running round.

可見性與遺失

23. As a LabVIEW 測試流程控制者, I want `check` to click the Log Solution Dock icon after 60 seconds without seeing Log Solution and look again 5 seconds later, so that a covered window is recovered automatically.
24. As a LabVIEW 測試流程控制者, I want at most 3 such recovery attempts per `check`, then `log_solution_not_visible`, so that I can call an operator instead of waiting for the full timeout.
25. As a LabVIEW 測試流程控制者, I want `round_lost` immediately when Standby appears after Monitoring was seen in this round, so that I pause and ask an operator instead of waiting for a result that will never come.
26. As a LabVIEW 測試流程控制者, I want `round_lost` to be an equipment/communication anomaly, not a product FAIL, so that products are not misjudged.
27. As an 操作員, I want the bridge's recovery clicks to be limited and only on the Dock icon, so that the ATE test program is not disturbed.

模板與 Monitor UI

28. As a 現場工程師, I want one shared Log Solution Dock icon template for all stations, so that I capture it once because it is the same program everywhere.
29. As a 現場工程師, I want 創建 Pattern to stop asking for `testing`, `pass` and `fail` templates, so that I only maintain templates used for operations.
30. As a 現場工程師, I want operation templates (station Dock icons, BT Start All, DFU OK, DFU checkboxes, DFU window) kept, so that operations keep working.
31. As an 操作員, I want Monitor UI's read-only check (`inspect`) on FCT and BT to read the Log Solution band too, so that what I see matches what LabVIEW gets.
32. As a 現場工程師, I want missing operation or Dock-icon templates to fail the command with a clear error before any HID action, so that misconfiguration never clicks blindly.

流程與文件

33. As a LabVIEW 測試流程控制者, I want the documented call order — DFU: `input` → `monitor_start` → `start_test` → `check`; BT: `monitor_start` → `start_test` → `check`; FCT: signal before fixture push → `monitor_start` → `check` — so that every station follows "monitor first, then test".
34. As a LabVIEW 測試流程控制者, I want `docs/PROTOCOL.md` to define `monitor_start`, `start_test`, the new error codes and the removed `button`, so that my typedefs match.
35. As a 部署人員, I want the deployment documents to state that ATE windows must not be maximised or cover the right side, that FCT must signal before fixture push, and that Log Solution profile channel counts must equal the device list, so that the site setup supports reliable reading.
36. As a Log Solution 開發者, I want the bridge to work with display contract 1.1 and 1.2 alike, so that our releases are independent.

## Implementation Decisions

- **單一判讀路徑**：`check` 與 `inspect` 對 DFU、FCT、BT 一律使用 Log Solution 顯示契約判讀（RoundFrameGate：先見 Monitoring、兩張新鮮一致完整畫面、`capacity` = 設備 `channels`、already_taken、Review → paused、凍結 → `frame_frozen`）。站別不再影響判讀，只影響操作指令。
- **淘汰模板判讀結果**：移除模板判讀結果的路徑（模板讀數映射到 `check` 結構的程式、`run_check` 的結果判定部分）、`testing`／`pass`／`fail` 模板規格與相關測試。保留操作用模板與流程（各站 Dock 圖示、BT Start All、DFU 視窗／checkbox／OK）。
- **共用 Dock 圖示模板**：模板目錄新增一個不分站別的 `log_solution_dock_icon`，創建 Pattern 可建立；`monitor_start` 與可見性恢復使用它。
- **`monitor_start`（新，三站，上鎖，無專屬欄位）**：
  1. 讀取最新畫面判讀 Log Solution 標記；為 Monitoring 或 Review 時回 `log_solution_busy`，不做 HID。
  2. 定位 Dock 上的 Log Solution 圖示，找不到回 `log_solution_not_found`；點擊後在數秒內確認畫面上找得到 Log Solution（契約定位方塊），找不到回 `log_solution_not_found`。
  3. 送 Cmd+Shift+M；在 10 秒內看到 Monitoring 即成功，否則 `monitor_not_started`。
  4. 成功時 `data` 含 `state = "monitoring"` 與 `capacity`；同時讓該設備的輪次判讀（RoundFrameGate）視為已看過 Monitoring。
  - 圖示確認等待、10 秒等待為可設定常數（測試可注入短值）。
- **`start_test`（新，DFU／BT，上鎖，無專屬欄位）**：先判讀 Log Solution 標記，非 Monitoring 回 `monitor_not_ready`；DFU 前景化 DFU 視窗並點 OK；BT 沿用 Start All 流程。定位或 HID 失敗回 `hid_failed`。FCT 回 `unsupported_command`。成功 `data` 含 `action`（`ok`／`start_all`）。取代並移除 `button`。
- **DFU `input` 變更**：只做前景化、checkbox 校正、逐筆輸入 SN；不點 OK、不送 Cmd+Shift+M。其餘驗證與 `log_solution_busy` 規則不變。
- **可見性恢復**：`check`／`inspect` 長輪詢中，若連續 60 秒判讀結果都是「找不到 Log Solution」（定位失敗類 unknown），點一次 Log Solution Dock 圖示並在 5 秒後重新判讀；同一請求最多 3 次，之後回 `log_solution_not_visible`。`inspect` 為唯讀，**不**點擊（只回報），以免 Monitor UI 操作 HID。時間與次數為可設定常數。點擊需取得該設備鎖範圍內執行（`check` 本來就持有鎖）。
- **`round_lost`**：同一串流上本輪已看過 Monitoring（或由 `monitor_start` 確認）後，判讀到 Standby → 立即回 `round_lost`，並清除該設備的 armed 狀態。Log Solution 1.2 下人工停止會顯示 Complete（含 `STOPPED`），正常交付；1.1 下人工停止會落入 `round_lost`。
- **錯誤碼（加入 PROTOCOL 1.0 錯誤碼表）**：`log_solution_not_found`、`monitor_not_started`、`monitor_not_ready`、`log_solution_not_visible`、`round_lost`；沿用 `log_solution_busy`、`hid_failed`、`unsupported_command`。移除 `button` 指令及其 `action` 欄位定義。協定版本維持 `1.0`（尚未上線）。
- **指令支援表（更新）**：`monitor_start` 三站；`start_test` DFU／BT；`input` DFU；`check`／`inspect`／`snapshot`／`status`／`ping` 不變。
- **文件**：更新 PROTOCOL（指令、欄位、錯誤碼、範例、typedef 清單、LabVIEW 呼叫順序）、DFU／FCT／BT workflow 文件（改為 Log Solution 判讀與新順序）、部署文件（ATE 視窗不遮右側、FCT 推入前訊號、Log Solution profile 通道數 = 設備清單、Log Solution 1.2 建議文件連結）、README 模板章節。
- **與 Log Solution 的關係**：不依賴契約 1.2；畫面幾何、圖樣、顏色不變。

## Testing Decisions

- 好的測試只驗證外部行為：送入的 JSON 行、回覆的 JSON 行、假 JetKVM 記錄到的點擊與按鍵順序；不斷言私有狀態或 log。
- **Seam（唯一主要切入點）— TCP JSON 協定＋合成 ATE 畫面**：測試中啟動真實 Core TCP 伺服器，注入設備清單，配合合成畫面的假 JetKVM，以真實 socket 收發。合成畫面需能：繪製 Log Solution 契約色帶與四種標記、Dock 上的 Log Solution 圖示、DFU OK、BT Start All；記錄點擊與按鍵；收到 Cmd+Shift+M 時由 Standby／Complete 轉為 Monitoring；設定 Log Solution「被遮住」或「未開啟」，並在點擊 Dock 圖示後可恢復；模擬監控中轉 Standby。時間常數（60 秒、5 秒、10 秒、3 次）由測試注入短值。
- 涵蓋：三站 `check`／`inspect` 判讀一致（含 FCT／BT taken、paused、already_taken、capacity_mismatch、未安裝 Log Solution 為 unknown）；`monitor_start` 成功與 `log_solution_not_found`／`monitor_not_started`／`log_solution_busy`；`start_test` DFU 點 OK、BT 點 Start All、FCT `unsupported_command`、`monitor_not_ready`、`hid_failed`；DFU `input` 不點 OK、不送快捷鍵；可見性恢復點擊次數上限與 `log_solution_not_visible`、`inspect` 不點擊；`round_lost`；`button` 已不存在（`unknown_command`）。
- Prior art：`test_check_1_0.py`、`test_inspect_1_0.py`、`test_dfu_input_1_0.py` 與 `dfu_helpers.py` 的合成 DFU／Log Solution 畫面、`test_bt_button_1_0.py` 的假 KVM 點擊記錄、`protocol_helpers.py` 的設備清單與 socket 輔助。
- 模板目錄沿用 `test_template_catalog.py`：結果模板已移除、共用 Log Solution Dock 圖示可建立與讀取。
- 淘汰模板判讀相關的既有測試（模板讀數映射、fail-closed 的模板分支、BT flow 的結果判讀部分）刪除或改寫；Start All 操作測試保留並改為經 `start_test`。
- 全套測試以本機權限執行（需要 bind localhost）。

## Out of Scope

- Log Solution 本身的修改（契約 1.2、視窗前景、FCT start 期限）：由 Log Solution 開發者依 `docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md` 自行開票。
- 設備端「治具推入前發訊號」的協調與 FCT 實際時序量測。
- 輪次 UUID、ACK、結果保存與重傳（協定 1.1）。
- Core／Monitor UI 穩定性修正 #17～#20（凍結檢查重連、連線池使用期、UI 通訊分流）。
- ADR-0004 與 Monitor UI 直接讀寫設備清單的衝突（另以 ADR 處理）。
- LabVIEW 端改版（#13）與雙語介面。

## Further Notes

- 本 spec 修改 spec #1 已實作的 1.0 指令（`button` 移除、`input` 行為改變）；1.0 尚未上線，因此不升版號，LabVIEW 改版（#13）應直接依更新後的 PROTOCOL 實作。
- `monitor_start` 的 Dock 點擊在 Log Solution 已關閉時會讓 macOS 重新開啟它；開啟後為 Standby，流程可繼續。
- 現場仍待確認：FCT 改為推入前發訊號後，訊號到首次寫檔的實際時間（決定 Log Solution FCT start 期限）；JetKVM 靜態 IP；9 台負載驗收。
