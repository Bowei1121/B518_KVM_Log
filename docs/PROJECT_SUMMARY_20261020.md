# 專案摘要 2026-10-20

接續 `PROJECT_SUMMARY_20261019.md`「第二輪架構評估」。本檔記錄換日時的進度與流程，供後續查閱。

## 前一日結論（2026-10-19）

- 第二輪架構評估：7 個候選；使用者選 C1～C4 grilling（Q1–Q29），開票 #54～#59，另開 #63（tests recv 迴圈收尾）。決策細節見 20261019 摘要。
- CONTEXT.md 新增「設備輪次」；`docs/FIELD_VERIFICATION.md` 集中所有待現場驗證項目（開發階段無法實機驗證）。
- 依檔案重疊重排順序：#54 改為 blocked by #57（`read_app_frame` 是取影格與讀畫面的交界，語意衝突）。

## 開發流程（使用者 2026-10-19 規定）

- 實作：獨立 session，Haiku 5.5，effort high。
- 審查：另一個獨立 session，Opus 5.5，effort medium，用 `/code-review`。
- 迴圈：實作 → 審查 → PR comment → 修正 → PR comment（逐點說明）→ 再審查 → … 直到審查結論「可合併」才 merge。
- merge 由主 session 執行，並同步 Gitea `origin` 的 main（`origin` 是 http，app 的同步工具不支援，改手動 push）。

## 票況（截至 2026-10-20 換日）

| 票 | PR | 狀態 |
| --- | --- | --- |
| #57 具名影格快照 | #62 | 已合併（`0511cfd`）；審查要求的 freeze_guard 不吞例外、`read_app_frame` 回 `(snapshot, observation)` 已併入 |
| #59 core_protocol（Grok） | #61 | 已合併（`1b82ee9`）。注意：審查 session 自行修正 `_pending`（`5f9ef81`）並合併，修正未經另一 session 再審；`a2e0740` 已完成 #63 大部分工作 |
| #54 Log Solution 讀取 | #65 | 審查 → 修 key 順序（`8d3e8e4`）→ 再審查「可合併」→ 已合併（`b9e8066`） |
| #58 KVM 斷線偵測 | #66 | 審查「修正後合併」，3 項必須修正，已交開發 session 修正 |
| #63 tests 收發收尾 | #67 | 待審查（已交審查 session） |
| #55 DeviceRound | #68 | 待審查（已交審查 session）；與 #66 都改 `_long_poll`，後合併者解衝突 |
| #56 重連後同輪重問 | — | blocked by #55 |

## 觀察

- 子 session 有時不依指示行事：兩個早期審查 session 未詢問即貼 comment；#59 審查 session 自行修正並合併。現行流程已明文授權貼 comment，但 merge 只由主 session 執行。
- 子 session 可能不採信主 session 轉達的跨 session 訊息（#57 session 曾暫停）；brief 內預先寫明流程可避免。
- 自動事件觸發的回合不能直接啟動新 session，只能提出啟動卡，需使用者點開。
