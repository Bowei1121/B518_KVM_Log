# B518 JetKVM — 專案摘要（2026-10-16）

日期：2026-10-16（Asia/Taipei）
前一日詳細紀錄：`docs/PROJECT_SUMMARY_20261015.md`。

## 2026-10-15 結果

- 確認 setup-matt-pocock-skills 已完成（GitHub Issues、五個 triage 標籤、single-context）。
- grilling 架構候選 1「統一設備 ID 與 Device Lock」（Q1–Q36）：設備 ID `<站別>-<兩位數>`、上位機 `devices.json` 為唯一設備清單、協定只帶設備 ID、測試通道數、Monitor UI 設定頁、長輪詢 0.5 秒與判讀移出事件迴圈等。
- 產出：`docs/PROTOCOL.md`（協定 1.0）、ADR-0007（Proposed）、CONTEXT.md 新詞彙、spec #1（本機 `docs/specs/0001-…`）、開發票 #2～#13（本機 `.scratch/device-identity-protocol-1.0/issues/`）。
- PR #14 fail-closed 修正（`check` 沒看到結果不再回 PASS）、PR #15 文件，皆已合併 main（`43706bf`）並同步 Gitea。
- 以 implement-spec 實作 #2～#12，整合分支 `feat/device-identity-protocol-1.0`，PR #16 已 ready、可合併，**尚未合併**。#13（LabVIEW）為人工票，使用者決定延後驗收。

## 尚未決定／留待後續的事項

| # | 項目 | 來源 | 性質 |
| --- | --- | --- | --- |
| 1 | 輪次 UUID、結果接收確認（ACK）、已確認輪次結果保存與 14 天保留、橋接重啟恢復、同輪重傳（協定 1.1） | 10-15 Q5、整合規格 | 需討論設計 |
| 2 | FCT／BT 判讀：模板判讀改善，或改用 Log Solution 顯示契約 | 10-15 Q21、Q24、架構候選 2 | 需討論設計 |
| 3 | 人員按停止監控後 Log Solution header 標記變成哪一種（影響 `STOPPED` 能否觸發 taken） | 10-15 Q22 | 現場確認 |
| 4 | JetKVM 正式版是否有靜態 IP 設定 | 10-15 Q8／Q9 | 現場確認 |
| 5 | 9 台同時運作的負載驗收（3 真機＋6 路重播，30 分鐘） | 10-15 Q28、Q33 | 現場執行 |
| 6 | 架構候選 3：Frame Freeze Guard 未區分重連串流（stream_id） | 10-14 架構評估 | 需確認 PR #16 後是否仍存在 |
| 7 | 架構候選 4：KVM Connection Pool 閒置淘汰可能關掉使用中的連線 | 10-14 架構評估 | 需確認 PR #16 後是否仍存在 |
| 8 | 架構候選 5：Monitor UI 通訊排程（共用 socket lock、status thread 累積） | 10-14 架構評估 | 需確認 PR #16 後是否仍存在 |
| 9 | ADR-0007 由 Proposed 改 Accepted | 10-15 | 使用者確認 |
| 10 | 雙語介面 | 10-15 A23 | 後續 |
| 11 | LabVIEW 端改版（#13） | 10-15 | 使用者延後 |

## 本日

- 使用者要求討論前一日留待後續、尚未決定的部分。

## 第 6～8 項查證（子代理唯讀，對 PR #16 `1a7e8c8`；關鍵點主線已核對）

- **6 凍結檢查 vs 重連：仍存在。** 凍結檢查狀態不記 `stream_id`，從未 reset；重連後新串流 PTS 從低值開始會被判 `frame_frozen`，直到超過舊值為止（實測 PTS 100→1 立即失敗）。
- **7 連線池使用期：仍存在。** `last_used_at` 只在取得連線時更新，heartbeat 不更新；`check` 長輪詢超過 600 秒且無其他流量時，連線會被淘汰關閉，接著多半誤報 `frame_frozen`（實測 held client 被 close）。
- **8 Monitor UI 通訊排程：仍存在，設定頁讓它更嚴重。** 所有功能共用一條 socket＋一把鎖；長時間 `inspect` 期間狀態、快照、串流、設定頁全部卡住；每 1.5 秒新開狀態執行緒會堆積；關閉視窗會卡在鎖上。
- **新發現：PR #16 的 FCT／BT `check` 永遠拿不到 taken。** `run_check` 回傳 3 欄（序號、結果、SN），`_template_observation` 以 2 欄拆解，看到任何 pass／fail 標記即 `ValueError` → `internal_error`；測試假資料用 2 欄所以沒抓到。另：同一格可能 pass／fail 重複計數、無兩張畫面穩定確認、無 already_taken。

## grilling：FCT／BT 判讀方式（第 1 輪，A1–A6）

- A1：FCT／BT 直接切換到 Log Solution 判讀，不再投資模板判讀。
- A2：Log Solution 視窗大小三站一致；結果色帶數量依專案與設備通道數而異（B518 三站皆 4；B482 DFU 有 20／7 通道、FCT 6、BT 4）；超過 10 通道顯示第二排，第二排由左至右為通道 11～N。顯示契約 1.1 已支援 1–20 格與兩排。
- A3：模板只用於操作定位（Dock、BT Start All、DFU checkbox）；結果判讀只看 Log Solution 色帶，`testing`／`pass`／`fail` 模板淘汰。
- A4：FCT／BT 的 SN 由 ATE 測試程式處理，橋接不輸入 SN；FCT／BT 監控啟動同樣是 Cmd+Shift+M。
- A5：PR #16 FCT／BT 列拆解 bug 已修：commit `55b7f93` 推上 `feat/device-identity-protocol-1.0`（兩個 remote），全套 334 passed。
- A6：第 6～8 項開票：parent #17，子票 #18（凍結檢查區分重連串流）、#19（連線池不淘汰使用中連線）、#20（Monitor UI 通訊分流），皆 `ready-for-agent`；本機副本 `.scratch/core-ui-stability/`。

## grilling：FCT／BT 判讀方式（第 2 輪，A7–A11）

- A7：DFU 點「OK」、BT 點「Start All」即開始測試，啟動權在 LabVIEW／橋接；DFU `input` 改為只填 SN、不點 OK。FCT 由測試儀器在治具推入後自動開始測試，收到訊號時可能已在測試中，FCT 不需任何點擊，只需開始監控。
- A8：新增三站共用 `monitor_start`（送 Cmd+Shift+M、等待畫面進入 monitoring）。快捷鍵是全域的，但只有 Log Solution 已開啟才有效。
- A9：`start_test`（DFU=OK、BT=Start All）；FCT 無需啟動。Log Solution 設計：開始監控前後 ±30 秒內產生的檔案都算同一輪；已派子代理唯讀查證 Log Solution 原始碼。
- A10：Log Solution 置於畫面最右側、ATE 測試程式在左側，盡量不遮擋；本輪監控完成後會回到最上層。
- A11：三站 `check` 統一走 Log Solution 判讀，淘汰模板判讀結果程式與 `testing`／`pass`／`fail` 模板。

## Log Solution 原始碼查證（子代理唯讀；標記狀態與 30 秒門檻主線已核對）

- **沒有對稱的 ±30 秒窗口。** 30 秒是寫死的下限（監控開始 − 30 秒），比對檔名或內容的時間戳，不看 mtime；主要防護是開始監控時的檔案快照（baseline），既有檔案要有變動才算。Atlas（DFU／FCT 共用）進行中的 `records.csv` 沒有時間限制；最終歸檔資料夾名稱時間戳須 ≥ 開始 − 30 秒。
- **FCT 先開測後監控的風險：** 開始監控後 30 秒內（start deadline）該通道 `records.csv` 沒變動就鎖成 `NOTEST`，之後結果被忽略；歸檔資料夾時間戳若是測試開始時間且早於監控 30 秒以上，最終結果永遠不被接受。歸檔時間戳是開始還是結束時間未確認。
- **人工停止監控後標記變成 `standby`**（`kvm_display_contract.py` `state_for_round_snapshot`），非終局格設為 `STOPPED`。橋接 RoundFrameGate 看到 standby 會解除 armed、回 waiting，所以**含 `STOPPED` 的輪次永遠不會被 `check` 取用**，與 10-15 A22 的處置設計衝突。
- 完成後標記停在 `complete`，直到下一次開始監控才直接進 `monitoring`。快捷鍵是全域、只能開始不能切換；監控中或待確認時按下會被忽略。
- 視窗在右上角，**沒有設定最上層**；只在單格出現 PASS／FAIL／NOTEST 時拉到前景，`TIMEOUT`、`STOPPED` 與整輪完成時不會。監控中可能被 ATE 視窗蓋住。
- 預期通道數來自 Log Solution 機台 profile（預設 DFU 7、FCT 6、BT 4），須與橋接 `devices.json` 通道數一致，否則 `capacity_mismatch`。

## grilling：FCT／BT 判讀方式（第 3、4 輪，A12–A19）

- A12：三站呼叫順序 — DFU：`input`（只填 SN）→ `monitor_start` → `start_test`（OK）→ `check`；BT：`monitor_start` → `start_test`（Start All）→ `check`；FCT：收到訊號 → `monitor_start` → `check`。
- A13：`start_test` 取代 PR #16 的 BT `button`；1.0 未上線，直接修改不升版。
- A14：`monitor_start` 先在 Dock 找 Log Solution 圖示，點擊後確認畫面上有 Log Solution 才送快捷鍵；找不到回 `log_solution_not_found`，送出後 10 秒未見 monitoring 回 `monitor_not_started`。
- A15：ATE 視窗不遮擋右側列入設備 SOP（與對方 TE 溝通）；`check` 長時間看不到 Log Solution 時先點 Dock 圖示拉回前面再判斷，仍看不到才回 `log_solution_not_visible`。
- A16／A18：採 Log Solution 修改方案（人工停止顯示 Complete、視窗前景時機），完整建議見 `docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md`。
- A17：FCT 治具推入不由 LabVIEW 控制；將與設備端協調改為推入前發訊號，LabVIEW 先 `monitor_start`。Log Solution FCT profile 的 start 期限需涵蓋訊號到首次寫檔的時間（建議先設 120 秒）。
- A19：Log Solution 的開票由其開發者依建議文件自行處理，本 repo 不開票。
