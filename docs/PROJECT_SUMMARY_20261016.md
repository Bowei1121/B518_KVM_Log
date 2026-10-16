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
