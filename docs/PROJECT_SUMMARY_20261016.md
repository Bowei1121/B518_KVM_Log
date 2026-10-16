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
