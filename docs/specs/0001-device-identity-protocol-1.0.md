# Spec: 統一設備 ID、設備清單與 LabVIEW TCP JSON 協定 1.0

GitHub issue：https://github.com/Bowei1121/B518_KVM_Log/issues/1（本檔為發布時的本機副本）

相關文件：ADR-0007（設備清單與統一設備 ID）、`docs/PROTOCOL.md`（TCP JSON 介面 1.0 草案）、`CONTEXT.md`、`docs/LABVIEW_LOG_SOLUTION_INTEGRATION.md`。保留 ADR-0002（Device Lock fail-fast）、ADR-0003（JSON Protocol 不向下相容）、ADR-0006（每設備一條連線）。

## Problem Statement

LabVIEW 測試流程控制者透過 Core Service 操作 9 台 ATE（DFU×7、FCT×1、BT×1），但目前「哪一台設備」在各處各說各話：

- LabVIEW 必須記住每台 JetKVM 的 IP 並隨指令傳送；換 KVM 或改 IP 就得改 LabVIEW 程式。
- Device Lock 只看 `device` 值，DFU、FCT、BT 預設都是 `1`，三站會互相回 BUSY，無法並行。
- Frame Freeze Guard、DFU 畫面判讀、匹配診斷、KVM Connection Pool 各用不同 key（`站別:設備號`、只用站別、IP），多台同站設備會共用狀態。
- `snapshot` 沒帶 IP 時用「站別或設備號任一符合」猜設定，FCT:1 會連到 DFU 的 KVM。
- 站別清單在 Core 與 Monitor UI 各寫死一份，DFU 4／7 格又另存一檔；現場沒有任何地方能集中維護設備設定。
- 協定回覆欄位依分支而異、可能省略或為 `null`，`req_id` 缺少時由 Core 自動補，LabVIEW 很難設計穩定的 type definition，也無法確認回覆屬於哪一筆請求。
- `input`／`button` 尚未接上 Core JSON；`check` 長輪詢遇到 Log Solution 待確認不會立即回覆。
- DFU 輸入依賴「每個通道一張錨點模板」，換成不同通道數的儀器就要重新截整套圖。

## Solution

每台設備有一個固定、跨站唯一的設備 ID（例如 `DFU-01`）。上位機上的一份設備清單記錄每台設備的站別、KVM IP 與測試通道數，是唯一來源。LabVIEW 只送設備 ID，Core 查表決定連哪台 KVM、用哪個站別邏輯；所有內部模組只認設備 ID。

協定升為 `protocol_version 1.0`：一行一筆 JSON、`req_id` 由 LabVIEW 產生並原樣回傳、每種回覆欄位固定、錯誤碼明確，LabVIEW 可照 `docs/PROTOCOL.md` 直接建立 typedef。`check` 只交付逐格結果，待確認立即通知，畫面格數必須等於設定通道數。

Monitor UI 改為從設備清單選擇設備，並新增設定頁讓現場人員以表格維護設備清單：儲存前驗證、自動備份、可逐台測試連線、有設備執行動作時不允許重啟 Core。

## User Stories

設備身份與協定

1. As a LabVIEW 測試流程控制者, I want to address a device only by its device ID such as `DFU-01`, so that my program never needs to know KVM IP addresses.
2. As a LabVIEW 測試流程控制者, I want requests carrying `kvm_ip` or `station` to be rejected with `forbidden_field`, so that I never believe an IP I sent was honoured when Core used another.
3. As a LabVIEW 測試流程控制者, I want device IDs matched exactly and case-sensitively, so that a typo like `dfu-01` fails loudly with `unknown_device` instead of reaching the wrong machine.
4. As a LabVIEW 測試流程控制者, I want commands that act on a device to be rejected with `missing_device` when no device ID is given, so that no command ever runs without a lock.
5. As a LabVIEW 測試流程控制者, I want `DFU-01`, `FCT-01` and `BT-01` to run commands at the same time without BUSY, so that the three stations test in parallel.
6. As a LabVIEW 測試流程控制者, I want a second locked command to the same device to return `device_busy` immediately, so that my state machine stays honest (ADR-0002).
7. As a LabVIEW 測試流程控制者, I want to generate `req_id` myself and have it required, so that I can match every reply to its request.
8. As a LabVIEW 測試流程控制者, I want every reply to echo `req_id`, `device` and `cmd`, so that I can verify a reply belongs to the request I sent before acting on it.
9. As a LabVIEW 測試流程控制者, I want each reply type to always contain the same fields with defaults instead of omissions or `null`, so that Unflatten From JSON with a fixed typedef never fails.
10. As a LabVIEW 測試流程控制者, I want unknown request fields rejected with `unknown_field`, so that misspelled field names are caught during development.
11. As a LabVIEW 測試流程控制者, I want a `protocol_version` field checked on every request, so that a mismatched program version is detected immediately.
12. As a LabVIEW 測試流程控制者, I want a documented, finite list of error codes with recommended actions, so that I can branch on `error` instead of parsing messages.
13. As a LabVIEW 測試流程控制者, I want a `status` command returning Core uptime and each device's connected/busy/frozen/channels state, so that my HMI can show connection status.
14. As a LabVIEW 測試流程控制者, I want `status` to omit KVM IPs, so that network details stay inside the bridge.
15. As a LabVIEW 測試流程控制者, I want `ping` to answer without a device ID, so that I can check Core is alive before opening per-device connections.

`check` 與結果

16. As a LabVIEW 測試流程控制者, I want `timeout_sec = 0` to return the current Log Solution state at once, so that I can poll cheaply.
17. As a LabVIEW 測試流程控制者, I want `timeout_sec > 0` (max 3600) to hold the request until a result, a 待確認 or an error appears, so that I do not flood Core with polls.
18. As a LabVIEW 測試流程控制者, I want a 待確認 (`review`) state to return `kind = paused` immediately during long polling, so that I can stop downstream equipment actions right away.
19. As a LabVIEW 測試流程控制者, I want the reply to carry the Log Solution marker `state`, so that my HMI can show standby/monitoring/review/complete.
20. As a LabVIEW 測試流程控制者, I want results only as per-slot `results[]` with no aggregate `result` field, so that product disposition is always decided slot by slot.
21. As a LabVIEW 測試流程控制者, I want `STOPPED` slots returned as-is, so that I can mark them defective and send them to rework while the audit trail shows they were manually stopped.
22. As a LabVIEW 測試流程控制者, I want `COMPLETING` treated as still waiting, so that I never take a half-finished round.
23. As a LabVIEW 測試流程控制者, I want `capacity_mismatch` when the screen's slot count differs from the device's configured channel count, so that a misconfigured Log Solution or a wrong screen never delivers results.
24. As a LabVIEW 測試流程控制者, I want a timed-out long poll to return `status = timeout` with the last observed state, so that I know what Core last saw.
25. As a LabVIEW 測試流程控制者, I want `unknown` (unreadable screen) never to be reported as a product FAIL, so that equipment faults are not confused with defective products.

`input` 與 `button`

26. As a LabVIEW 測試流程控制者, I want to send SNs as `items[{slot, sn}]`, so that SNs containing commas or colons are transmitted safely.
27. As a LabVIEW 測試流程控制者, I want `input` rejected with `log_solution_busy` while Log Solution is monitoring or in review, so that next-round SNs are never typed into a running round.
28. As a LabVIEW 測試流程控制者, I want `input` to report which slots were actually entered, so that I can verify the input step.
29. As a LabVIEW 測試流程控制者, I want slot numbers in `input` to match Log Solution Slot numbers, so that SN-to-result mapping needs no translation.
30. As a LabVIEW 測試流程控制者, I want `button` with `action = start_all` for BT, so that I can start BT tests through the same protocol.
31. As a LabVIEW 測試流程控制者, I want unsupported station/command combinations to return `unsupported_command`, so that mistakes are explicit.

DFU 輸入定位

32. As a 現場工程師, I want DFU input to locate checkboxes using only checked/unchecked templates, so that changing channel count does not require new per-slot templates.
33. As a 現場工程師, I want DFU input to require exactly N checkboxes found and refuse to type anything otherwise, so that a partially visible window never gets wrong SNs.
34. As a 現場工程師, I want checkboxes ordered top-to-bottom then left-to-right as channels 1–N, so that ordering matches the test program and Log Solution.

設備清單

35. As a 現場工程師, I want one device list file on the host PC holding device ID, station, KVM IP and channel count, so that all device configuration lives in one place.
36. As a 現場工程師, I want Core to refuse to start when the device list is missing or invalid, and say exactly what is wrong, so that misconfiguration never runs silently.
37. As a 現場工程師, I want duplicate IDs, duplicate IPs, malformed IPs, unknown stations, ID prefix/station mismatch and invalid channel counts rejected, so that the list is always consistent.
38. As a 現場工程師, I want device IDs restricted to `<站別>-<兩位數>`, so that naming stays uniform across projects.
39. As a 現場工程師, I want changes to take effect only after a Core restart, so that a running `check` is never disrupted by an IP change.
40. As a 現場工程師, I want the old DFU profile file and hard-coded station lists retired, so that there is no second source of truth.

Monitor UI

41. As an 操作員, I want to pick a device from the list Core provides instead of typing an IP, so that I cannot connect to the wrong machine.
42. As an 操作員, I want manual checks in Monitor UI to take no lock and not consume the round's result, so that I never block or steal LabVIEW's `check`.
43. As a 現場工程師, I want a settings page with a table of device ID, station, KVM IP and 測試通道數 (numeric entry), so that I can maintain the device list without editing JSON.
44. As a 現場工程師, I want validation errors shown under the offending row before saving, so that I can fix mistakes immediately.
45. As a 現場工程師, I want a per-row and "test all" connection test, so that I can verify each KVM IP before restarting Core.
46. As a 現場工程師, I want the previous file backed up automatically on save, so that I can roll back a bad change.
47. As a 現場工程師, I want a restart-Core button that refuses while any device is executing an action, so that a round in progress is never interrupted.
48. As a 現場工程師, I want the settings page to work even when Core is not running, so that I can create the first device list.
49. As a 現場工程師, I want the first opening without a device list to prefill B518's nine devices with planned IPs and 4 channels, so that initial setup is quick.
50. As an 操作員, I want buttons with icons plus short labels, so that the UI is easy to use on the line.

效能與部署

51. As a 現場工程師, I want long polling to inspect frames every 0.5 s, so that nine devices can be monitored without wasting CPU.
52. As a 現場工程師, I want frame inspection to run off the main event loop with a per-frame time limit, so that one slow screen cannot stall other devices or TCP replies.
53. As a 現場工程師, I want a replay tool that feeds recorded frames into the pool as simulated KVMs, so that nine-device load can be measured with only three real ATEs.
54. As a 部署人員, I want a static IP plan for nine JetKVMs and the host PC on the unmanaged switch, so that the device list stays valid.

## Implementation Decisions

- **設備清單模組（新，深模組）**：一個獨立模組負責讀取與驗證設備清單檔，對外只有「給路徑 → 回傳設備清單或錯誤列表」。錯誤列表逐項指出列號／設備 ID 與原因。設備清單提供：依設備 ID 查詢（站別、KVM IP、通道數）、依序列出全部設備。Core 啟動與 Monitor UI 設定頁共用同一份驗證規則；設定頁另外使用「寫入（含備份）」功能。
- **設備清單檔 schema**：物件，key 為設備 ID，值含 `station`（`DFU`／`FCT`／`BT`）、`kvm_ip`（IPv4）、`channels`（正整數）。驗證規則：ID 符合 `<站別>-<兩位數>` 且前綴等於 `station`；ID 與 IP 皆不可重複；站別限三種；通道數 ≥ 1。檔案位於使用者模板根目錄，不隨程式打包、不進 Git。
- **Core Service 啟動**：缺檔或驗證失敗即拒絕啟動並輸出錯誤列表。設定變更需重啟 Core；不做執行中重新載入。
- **TCP JSON 伺服器**：建構時注入設備清單（取代寫死的站別設定）。請求驗證順序：JSON → 物件 → `protocol_version` → `req_id` → `cmd` → 欄位白名單（`unknown_field`／`forbidden_field`）→ 設備 ID 存在 → 站別支援該指令 → 上鎖 → 依查表 IP 取得 KVM 連線 → 執行。
- **固定回覆 envelope**：每筆回覆含 `protocol_version`、`req_id`、`device`、`cmd`、`status`（ok／error／timeout）、`error`、`message`、`elapsed_sec`、`data`。`data` 依指令固定欄位，錯誤時仍以預設值出現；不使用 `null`。完整欄位、範例與錯誤碼以 `docs/PROTOCOL.md` 為準。
- **LabVIEW 指令**：`ping`、`status`、`snapshot`、`check`、`input`（DFU）、`button`（BT）。原完整內部狀態改名 `core_status`，與 `shutdown` 僅供 Monitor UI／管理工具，不列入 LabVIEW 文件。
- **`core_status` 心跳狀態欄位（2026-10-27 補記，#128／#130／#125）**：`stations[]` 帶兩個 Core 鎖定的布林欄位給 Monitor UI：`is_heartbeat_stopped`（心跳停止）與 `is_heartbeat_not_found`（找不到心跳框，Heartbeat not found：影格在前進但心跳框讀不到；#125 稱 heartbeat unreadable）。兩者語意與處理方式相同：判定後鎖定為 `true`，恢復條件出現才解除，不改變 `status` 或指令拒絕規則，可同時為 `true`。`is_heartbeat_not_found` 的偵測屬 #125，實作前 Core 一律送 `false`。Monitor UI 依優先序「畫面凍結 > 心跳停止 > 找不到心跳框 > 執行指令中／已連線」顯示紅色列。欄位定義以 `docs/PROTOCOL.md` 為準。
- **單一 key**：Device Lock、Frame Freeze Guard、DFU RoundFrameGate、`run_check` 內的凍結檢查、匹配診斷目錄全部改用設備 ID。KVM Connection Pool 仍以 IP 管理連線，IP 一律由設備清單查得；一對一部署下鎖設備即鎖 KVM。移除 `snapshot` 的站別／設備號猜測。
- **`check`**：依 Log Solution 顯示契約 1.1 判讀，回 `kind`（taken／waiting／paused／already_taken／unknown）、`state`、`reason`、`capacity`、`results[]`。長輪詢於 taken、paused、already_taken 或錯誤時立即回覆。`STOPPED` 視為終局狀態可觸發 taken；`COMPLETING` 視為等待。taken 時 `capacity` 必須等於設備 `channels`，否則 `capacity_mismatch`。移除總結果欄位。FCT／BT 過渡期仍用模板判讀，但輸出映射到同一 `check` 回覆結構。
- **`input`（DFU）**：接收 `items[{slot, sn}]`；Log Solution 為 monitoring／review 時回 `log_solution_busy` 且不做任何 HID。DFU 輸入定位改為：只載入 checked／unchecked checkbox 模板，在 DFU 視窗內找出全部 checkbox，數量須等於 `channels`，以 y 分排（容許約半個 checkbox 高）、排內依 x 排序為通道 1～N。不再使用每通道錨點模板。
- **`button`（BT）**：`action = start_all`，沿用既有 BT Start All 流程，註冊為 Core 正式指令。
- **長輪詢與判讀執行**：輪詢間隔 0.5 秒；畫面判讀移至背景執行緒（或程序）執行，單張超過時間上限即記為 unknown 並取下一張，不阻塞事件迴圈。
- **Monitor UI**：移除手動 IP 欄位，設備下拉改由 Core `core_status` 提供。手動檢查以唯讀模式呼叫（不上鎖、不改變 RoundFrameGate 的 taken／armed 狀態）。新增設定頁：表格欄位為設備 ID、站別、KVM IP、測試通道數（數字輸入）；儲存前用設備清單模組驗證，錯誤標示在該列；儲存時備份舊檔（時間戳檔名）；逐列與全部測試連線；「重啟 Core」在任一設備忙碌時拒絕；首次無檔時預填 B518 九台（通道數 4、IP 依規劃）。設定頁直接讀寫本機檔案，Monitor UI 與 Core 必須同機。按鈕採圖示＋簡短文字。
- **網路部署**：EKI-2720G 為非網管交換機，九台 JetKVM 與上位機手設靜態 IP；建議規劃上位機 .10、DFU-01～07 .70–.76、FCT-01 .77、BT-01 .78（同網段）。
- **舊格式清理**：README 逗號協定範例與舊 TCPIP 測試 VI 更新為 JSON 1.0。

## Testing Decisions

- 好的測試只驗證外部行為：送進去的 JSON 行與回覆的 JSON 行、設備清單檔與驗證結果、合成畫面與排序結果；不斷言內部屬性、私有方法或 log 內容。
- **Seam 1（主要）— TCP JSON 協定**：測試中啟動真實 TCP JSON 伺服器，注入測試用設備清單與假 KVM client factory，透過真實 socket 收發 JSON 行。涵蓋：設備 ID 驗證與錯誤碼、`forbidden_field`／`unknown_field`／`missing_req_id`／`unsupported_protocol_version`、跨站不互鎖與同設備 `device_busy`、所有回覆 envelope 欄位固定、`status` 內容、`check` 各 `kind`／長輪詢立即回 paused／`capacity_mismatch`／`STOPPED` 與 `COMPLETING`、`input` 遇 monitoring／review 回 `log_solution_busy`、站別不支援指令。Prior art：既有 TCP server、long polling、KVM pool、device lock、freeze guard 的 socket 測試與 fake client factory 寫法；`check` fail-closed 測試。
- **Seam 2（新增）— 設備清單載入與驗證**：以暫存目錄中的 JSON 檔測試合法清單、缺檔、每一種驗證錯誤（含列號／ID 指向）、備份寫入。這是唯一新增的 seam，因為 Core 與 Monitor UI 設定頁都依賴它，且設定頁需在 Core 未啟動時使用。
- **Seam 3（沿用）— DFU 輸入流程**：沿用既有 DFU flow 測試，以合成畫面驗證 checkbox 數量須等於 N、不等即不做 HID、由上而下由左至右排序為 1～N。
- Monitor UI 的 Tk 畫面不做自動化測試；其驗證邏輯由 Seam 2 覆蓋，與 Core 的通訊沿用既有 UI client 測試寫法。
- 效能以錄影重播壓測工具量測（3 台真機＋6 路模擬，記錄 CPU、記憶體、畫面時間戳延遲 30 分鐘），作為上線驗收而非單元測試。
- 全套測試需以本機權限執行（需要 bind localhost）。

## Out of Scope

- 輪次 UUID、結果接收確認（ACK）、已確認輪次結果保存與 14 天保留、橋接重啟恢復與同輪重傳（另案，屆時以設備 ID 為 key，協定升 1.1）。
- FCT／BT 改用 Log Solution 判讀，以及模板判讀演算法改善（下一議題）。
- 雙語介面。
- 執行中自動重新載入設備清單、遠端（跨電腦）編輯設定。
- JetKVM 韌體升級與靜態 IP 實機設定作業本身（列入部署文件）。

## Further Notes

- 已完成的前置修正：FCT／BT／DFU `check` 在沒看到結果時不再回 PASS（分支 `claude/elated-chaplygin-31da83`，commit `b6b01b6`，尚未合併 main）；本 spec 實作應以合併後的版本為基礎。
- 本 spec 與 LabVIEW 端需同步改版；協定 1.0 不相容舊逗號格式與現行 JSON 欄位。
- JetKVM 正式版是否提供靜態 IP 設定選項待現場確認（社群資料指 0.5.0 dev 起支援）。
- 人員按停止監控後 Log Solution header 標記會變成哪一種待現場確認；若維持 monitoring，`STOPPED` 結果需另外處理才能觸發 taken。
- ADR-0007 目前為 Proposed，待使用者確認後改為 Accepted。
