# Spec: 橋接協定修訂：測試開始確認、測試結束確認與 Log 1.2 狀態

GitHub issue：https://github.com/Bowei1121/B518_KVM_Log/issues/40（本檔為發布時的本機副本）

相關文件：`docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md`（與 Log Solution 主文件同步）、`docs/PROJECT_SUMMARY_20261019.md`「grill-with-docs：橋接協定修訂（Q1–Q19）」、`docs/PROTOCOL.md`、`CONTEXT.md`（Log 儀器狀態、測試開始確認、測試結束確認、本輪開始請求、同輪重傳）、spec #1、spec #21。保留 ADR-0002（fail-fast）、ADR-0003（JSON 不向下相容）、ADR-0005（Frame Freeze Guard）、ADR-0006（每設備一條連線）、ADR-0007（設備清單與統一設備 ID）。取代 #36。

## Problem Statement

LabVIEW 測試流程控制者需要分兩步確認一輪測試：先確認 ATE 真的開始測試，再取得完整結果；Log Solution 1.2 也新增了準備中、啟動失敗異常與人工停止可交付等狀態（HANDOFF 已確認）。目前橋接程式：

- 只有單一 `check`，無法分辨「Log 已監控就緒」與「ATE 已開始測試」；只看到監控中時 LabVIEW 無從得知 ATE 是否當機。
- 不認得準備中（0101）：`monitor_start` 送出快捷鍵後只等 10 秒，準備較久就誤報 `monitor_not_started`；`check` 期間把準備中當成「Log Solution 不可見」，30 秒後去點 Dock。
- `monitor_start` 送出快捷鍵後遇到 Log 異常待確認（啟動失敗等 REVIEW），要白等到逾時才回報。
- 不認得人工停止可交付（0011），人工停止的輪次交付不到 LabVIEW。
- 送過開始監控、卻一直沒看到本次監控就緒（人工停止或準備逾時）時，畫面出現可交付結果也無法安全判斷是否屬於本輪；目前只會一直等待。
- 結果一旦取用，再查只回 `already_taken` 且不帶結果，LabVIEW 無法重看或在通訊中斷後重取同一輪結果。

## Solution

協定 1.0（尚未上線）直接修訂：移除 `check`，改為 `check_start`（測試開始確認）與 `check_finish`（測試結束確認），兩者與 `inspect` 共用同一個回覆格式。橋接程式辨識 Log Solution 1.2 的六種整輪狀態，`monitor_start` 等候準備中並在異常時立即回報。橋接以「本輪開始請求」區分「已要求開始但未見監控就緒」與「尚未要求開始」。第一次交付回 `taken`，之後同一輪可隨時重看，回 `already_taken` 並附相同結果，但只有 `taken` 可觸發設備動作。另提供給 LabVIEW 設計者的交接文件，說明呼叫順序與每種回覆該做什麼。

## User Stories

開始與結束確認

1. As a LabVIEW 測試流程控制者, I want a `check_start` command that confirms at least one channel has actually started testing, so that I can tell ATE really started rather than only that Log is ready.
2. As a LabVIEW 測試流程控制者, I want `check_start` to list each started channel with its evidence (`testing` or `result`), so that I know which positions are running and how the bridge knows.
3. As a LabVIEW 測試流程控制者, I want NOTEST, TIMEOUT, STOPPED and WAITING never counted as activity, so that a dead ATE is not reported as started.
4. As a LabVIEW 測試流程控制者, I want a channel counted as started only when two consecutive fresh frames both show it active, so that one noisy frame cannot fake a start.
5. As a LabVIEW 測試流程控制者, I want `check_start` to deliver the full result directly when the round is already complete, so that I do not have to send another command for fast tests.
6. As a LabVIEW 測試流程控制者, I want `check_start` to be optional and repeatable, returning the latest active channels without consuming the result, so that I can poll progress or skip it.
7. As a LabVIEW 測試流程控制者, I want `check_finish` to return the whole round's per-slot results once complete and verified, so that I make product disposition decisions.
8. As a LabVIEW 測試流程控制者, I want both commands to require `timeout_sec` (0–3600) with the same long-poll and timeout semantics as before, so that my waiting logic is unchanged.
9. As a LabVIEW 測試流程控制者, I want `timeout_sec` documented as my wait time, distinct from Log Solution's test deadlines, so that I do not confuse the two.
10. As a LabVIEW 測試流程控制者, I want the old `check` removed and answered with `unknown_command`, so that there is one unambiguous way to confirm a round.
11. As a LabVIEW 測試流程控制者, I want one shared reply shape for `check_start`, `check_finish` and `inspect`, so that I build a single typedef.

回覆種類

12. As a LabVIEW 測試流程控制者, I want `kind = started` to mean only "testing has started on the listed channels", so that I do not mistake it for a result.
13. As a LabVIEW 測試流程控制者, I want `kind = taken` only on the first delivery of a round's complete result, so that I trigger equipment actions exactly once.
14. As a LabVIEW 測試流程控制者, I want later reads of the same round to return `already_taken` with the identical results, so that I can re-view or re-fetch after a communication break.
15. As a LabVIEW 測試流程控制者, I want `already_taken` to be recognised only while the screen still shows the same round (same completion kind, capacity and per-slot results, no new round started), so that a new round is never mistaken for a re-view.
16. As a LabVIEW 測試流程控制者, I want `waiting`, `paused` and `unknown` to keep their meanings, so that my handling of preparation, review and unreadable screens stays simple.
17. As a LabVIEW 測試流程控制者, I want `state = manual_stop_complete` on a manual-stop delivery, so that I report the batch as manually stopped and send it to rework.
18. As a LabVIEW 測試流程控制者, I want `state = preparing` reported while Log is preparing, so that my HMI shows why nothing has started yet.

本輪開始請求與同輪保護

19. As a LabVIEW 測試流程控制者, I want `manual_verification_required` when I have requested start for this round but the bridge never saw this round's monitoring and a deliverable screen appears, so that leftover or unverifiable results are never used automatically.
20. As a LabVIEW 測試流程控制者, I want this to cover both a manual stop before monitoring and a preparation timeout acknowledged by an operator, so that both follow the same safe path.
21. As a LabVIEW 測試流程控制者, I want `monitor_not_ready` when I call `check_start`/`check_finish` without having requested start for this round, so that a flow-order mistake is explicit and old results are not taken.
22. As a LabVIEW 測試流程控制者, I want the start request cleared after a delivery or after `manual_verification_required`, so that the next round must start with a fresh `monitor_start`.
23. As a 操作員, I want the bridge never to auto-retest or force-deliver after `manual_verification_required`, so that I decide rework after checking.

開始監控

24. As a LabVIEW 測試流程控制者, I want `monitor_start` to take a required `timeout_sec` (max 300), so that I give slow-preparing stations enough time.
25. As a LabVIEW 測試流程控制者, I want `monitor_start` to keep waiting while Log shows preparing, so that slow source preparation does not fail the start.
26. As a LabVIEW 測試流程控制者, I want `monitor_start` to resume waiting without resending the shortcut when Log is already preparing, so that a retried start does not get stuck or double-trigger.
27. As a LabVIEW 測試流程控制者, I want `log_solution_review` returned at once when Log shows review before or after the shortcut, so that I raise an alarm instead of waiting for a timeout.
28. As a LabVIEW 測試流程控制者, I want `log_solution_busy` kept for monitoring, so that "Log is busy" and "Log needs an operator" are distinguishable.

其他指令遇到準備中

29. As a LabVIEW 測試流程控制者, I want DFU `input` to return `log_solution_busy` while preparing, so that SNs never enter a round being set up.
30. As a LabVIEW 測試流程控制者, I want `start_test` to return `monitor_not_ready` while preparing, so that ATE never starts before monitoring is ready.
31. As a 現場工程師, I want preparing counted as a reliable reading that resets the visibility timer, so that the bridge does not click the Dock while Log is legitimately preparing.

辨識

32. As a LabVIEW 測試流程控制者, I want the bridge to recognise all six whole-round patterns (standby 1001, preparing 0101, monitoring 1100, review 1010, complete 0110, manual-stop complete 0011) and treat any other pattern as unknown, so that reading follows the agreed contract.
33. As a LabVIEW 測試流程控制者, I want complete and manual-stop-complete frames never combined in the stability check, so that a delivery's completion kind is always correct.
34. As a LabVIEW 測試流程控制者, I want manual-stop-complete delivered even when every slot already has a final result and none is STOPPED, so that the stop reason is never lost.
35. As an 操作員, I want Monitor UI `inspect` to show started channels using the same shape, without locking, consuming results or clicking, so that I see progress safely.

文件

36. As a LabVIEW 設計者, I want a handoff document listing call order per station, the six kinds with required actions, error handling and when equipment actions are allowed, so that I implement LabVIEW correctly.
37. As a LabVIEW 設計者, I want PROTOCOL.md to define the new commands, fields, kinds, error codes and examples consistently with the handoff, so that my typedefs match.

## Implementation Decisions

- **指令集**：移除 `check`；新增 `check_start`、`check_finish`（皆上鎖、device-bound、必填 `timeout_sec` 0–3600）。`inspect` 保留唯讀、不上鎖。協定版本維持 `1.0`；不留別名。`monitor_start` 新增必填 `timeout_sec`（1–300）。
- **共用回覆 `data`**（`check_start`、`check_finish`、`inspect`）：`kind`、`state`、`reason`、`capacity`、`active`、`results`；固定欄位、無 `null`。
  - `kind`：`started`／`taken`／`already_taken`／`waiting`／`paused`／`unknown`。`started` 只來自 `check_start` 與 `inspect`；`inspect` 為唯讀，不會回 `taken`。
  - `state`：`standby`／`preparing`／`monitoring`／`review`／`complete`／`manual_stop_complete`／`""`。
  - `active`：`[{slot, evidence}]`，`evidence` 為 `testing`（TESTING 或 COMPLETING）或 `result`（PASS／FAIL 等有效測試結果且未見測試中）；NOTEST／TIMEOUT／STOPPED／WAITING 不算。
  - `results`：`taken` 與 `already_taken` 時為完整逐格結果，其餘為 `[]`。
- **狀態標記解碼**：辨識六種兩黑兩白圖樣（1001、0101、1100、1010、0110、0011），其餘、缺格、低對比為 unknown。幾何仍為 1.1（新定位點位置屬 #32）。
- **輪次判讀模組（RoundFrameGate）擴充**：
  - 完成種類（一般／人工停止）納入穩定簽章：完成種類＋容量＋逐格結果，兩張相同才交付；種類切換重新計數。
  - 交付後記住該輪簽章；畫面仍為同一簽章且未開始新輪 → `already_taken` 附相同結果；看到 Monitoring／Preparing 或收到新 `monitor_start` 即視為新輪。
  - 活動判定：連續兩張新鮮影格（序號前進、間隔 ≤ 1 秒）皆有活動的通道取交集；中間出現 unknown 重新計算。
  - Preparing 為可靠讀數、`kind = waiting`；Review 為 `paused`；已見本次 Monitoring 後出現 Standby 仍為 `round_lost`。
- **本輪開始請求**（每設備，Core 存活期間）：`monitor_start` 送出快捷鍵即建立；交付（`taken`）或回 `manual_verification_required` 後清除。
  - 有請求、未見本次 Monitoring、出現可交付畫面（Complete 或 ManualStopComplete，全部終局）→ `error = manual_verification_required`，不交付、不點 Dock、不自動重測。
  - 無請求 → `check_start`／`check_finish` 回 `error = monitor_not_ready`，不取用畫面殘留結果。
  - 有請求且已見本次 Monitoring → 正常判讀。
- **`check_start`**：已見完整可交付結果 → 直接 `taken`（或 `already_taken`）；活動通道經兩影格確認 → `started`；否則依狀態 `waiting`／`paused`／`unknown` 或長輪詢至逾時。可重複呼叫，不消耗結果。
- **`check_finish`**：只在完整可交付結果通過驗證時回 `taken`（第一次）或 `already_taken`；不回 `started`。容量不符 → `capacity_mismatch`。
- **`monitor_start`**：開始前 Monitoring → `log_solution_busy`；Review → `log_solution_review`（新錯誤碼，不送快捷鍵）；Preparing → 不送快捷鍵、直接等待 Monitoring。Standby／Complete／ManualStopComplete → 照既有 Dock 流程送快捷鍵。送出後 Preparing 繼續等，Review 立即 `log_solution_review`，`timeout_sec` 內未見 Monitoring → `monitor_not_started`。
- **其他指令遇 Preparing**：`input` → `log_solution_busy`；`start_test` → `monitor_not_ready`。
- **可見性恢復**：沿用 #31（排除法、30 秒、最多 3 次 Dock），套用於 `check_start`、`check_finish`；`inspect` 不點擊；Preparing 屬可靠讀數。
- **錯誤碼表**：新增 `log_solution_review`；`monitor_not_ready` 擴充為「未送本輪 `monitor_start` 就要求確認」；`manual_verification_required` 依本輪開始請求規則。
- **文件**：PROTOCOL.md 改寫指令、欄位、`kind` 表、錯誤碼、範例、typedef 清單、三站呼叫順序；新增 `docs/LABVIEW_HANDOFF.md`（給 LabVIEW 設計者）並與 PROTOCOL 的 `kind` 表一致。
- **Monitor UI**：手動檢查改用新格式顯示 `state` 與已開始通道。
- **補充決定（2026-10-19，實作前釐清）**：
  1. **準備中也建立本輪開始請求**：`monitor_start` 開始時若已是準備中，不送快捷鍵，但要建立（或保留）該設備的本輪開始請求，接著等待監控就緒；同一次呼叫內看到監控中即視為已見本次監控就緒。`monitor_start` 回 `monitor_not_started` 時不清除本輪開始請求；只有 `taken` 或 `manual_verification_required` 會清除。
  2. **`check_start`／`check_finish` 的判斷順序**（依序套用，先符合者回覆）：
     1. 待確認（REVIEW）→ `kind = paused`。
     2. 畫面簽章（完成種類＋格數＋逐格結果）與上一次交付相同，且之後沒有新一輪信號 → `kind = already_taken`，附相同結果。此條排在本輪開始請求檢查之前，交付後請求已清除仍可重看。
     3. 畫面不可靠 → `kind = unknown`（照常套用可見性恢復）。
     4. 沒有本輪開始請求 → `error = monitor_not_ready`。
     5. 有請求但未見本次監控就緒，出現完成或人工停止完成 → `error = manual_verification_required`；有請求且已見本次監控就緒 → 正常判讀。
     - 新一輪信號：看到準備中或監控中，或收到新的 `monitor_start`；任一出現即清除「上一次交付的簽章」。
     - 因此交付後簽章改變、沒有新一輪信號、請求已清除 → `monitor_not_ready`（原結果已不在畫面上，不能再當 `already_taken`；新內容未經開始流程，不能交付）。
  3. **人工停止完成後的 DFU `input`**：人工停止完成與一般完成同屬 READY，規則相同。待命 → 可輸入；完成或人工停止完成且本輪已交付（`taken`）或已回 `manual_verification_required` → 可輸入下一輪 SN；完成或人工停止完成但本輪尚未交付、也沒回過 `manual_verification_required` → `log_solution_busy`；監控中、準備中、待確認 → `log_solution_busy`。

## Testing Decisions

- 好的測試只驗證外部行為：送入的 JSON 行、回覆的 JSON 行、假 JetKVM 記錄的點擊與按鍵；不斷言私有狀態或 log。
- **主要切入點（沿用 spec #2）**：TCP JSON 協定＋合成 ATE 畫面的假 JetKVM，以真實 socket 收發。合成畫面擴充：六種狀態圖樣（含 Preparing 0101、ManualStopComplete 0011）、通道 TESTING／COMPLETING 顏色、可逐張改變的畫面序列（模擬兩影格、unknown 夾雜、完成種類切換）、`monitor_start` 送快捷鍵後依序呈現 Preparing → Monitoring 或 Preparing → Review。
- 涵蓋：補充決定 1～3 各至少一個案例（準備中起點建立請求、`monitor_not_started` 後請求仍在、交付後重看不受請求清除影響、交付後簽章改變回 `monitor_not_ready`、人工停止完成交付後 `input` 可用而未交付時 `log_solution_busy`）；`check` 已移除；`check_start` 的兩影格交集、evidence 區分、非活動狀態、直接交付、重複呼叫不消耗；`check_finish` 的 `taken`／`already_taken`（相同結果、新輪後不再是 already_taken）；本輪開始請求三種情況與清除；`manual_verification_required` 的人工停止與準備逾時兩案；`monitor_start` 的 Preparing 等待、Preparing 起點不送快捷鍵、Review 立即錯誤、`timeout_sec` 逾時；`input`／`start_test` 遇 Preparing；Preparing 重置可見性計時；六種圖樣與未知圖樣；完成種類不混用；`inspect` 新格式且不點擊。
- Prior art：`test_check_1_0.py`、`test_monitor_start.py`、`test_start_test_1_0.py`、`test_dfu_input_1_0.py`、`test_visibility_recovery.py`、`test_inspect_1_0.py`、`test_round_frame_consumer.py`、`protocol_helpers.py`（LogSolutionKVM、render_log_solution、cover_left）。既有 `check` 測試改寫為新指令。
- 全套測試以本機權限執行（需要 bind localhost）。

## Out of Scope

- Log Solution 本身的修改（圖樣、準備中、復位、視窗前景、FCT 期限、新定位點）。
- #32 左定位點新位置（等座標）、#34 顏色釘住。
- 協定 1.1：輪次 UUID、結果接收確認（ACK）、橋接重啟後重傳、HANDOFF 9.4 輪次取消與新輪次 ID。
- LabVIEW VI 與 typedef 實作（#13，人工）。
- 部分通道開始時的逐通道期限政策（Log Solution 負責）。

## Further Notes

- 1.0 尚未上線，因此直接修改不升版；LabVIEW 改版應直接依本 spec 完成後的 PROTOCOL 與 `docs/LABVIEW_HANDOFF.md`。
- 部署順序：先更新橋接（支援六種圖樣），再部署 Log Solution 1.2；1.1 的 App 下橋接仍可運作（不會出現 0101／0011）。
- `already_taken` 只在 Core 存活期間成立；Core 重啟後的重傳屬協定 1.1。
- #36 由本 spec 取代並關閉。
