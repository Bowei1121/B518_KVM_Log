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

## 票況（2026-10-20 收尾）

全部完成，main 在 `4e24a0e`，GitHub 與 Gitea 同步；整合後 main 全套 **589 passed, 34 subtests**。

| 票 | PR | 審查輪次 | 合併 |
| --- | --- | --- | --- |
| #57 具名影格快照 | #62 | 審查 → 修正（freeze_guard 不吞例外、`read_app_frame` 回 `(snapshot, observation)`） | `0511cfd` |
| #59 core_protocol（Grok） | #61 | 審查 → 審查 session 自行修 `_pending` 並合併（未經再審） | `1b82ee9` |
| #54 Log Solution 讀取 | #65 | 審查（key 順序）→ 修正 → 再審「可合併」 | `b9e8066` |
| #63 tests 收發收尾 | #67 | 審查「可合併」 | `5d2db24` |
| #58 KVM 斷線偵測 | #66 | 審查（3 項）→ 修正 → 再審「可合併」 | `a970e76` |
| #55 DeviceRound | #68 | 審查（缺 SN 放行測試）→ 修正 → 再審「可合併」（含與 #66 試合併） | `4edb0b9` |
| #56 重連後同輪重問 | #70 | 審查「可合併」（7 個 mutation 皆被抓到） | `4e24a0e` |

新開：**#69**（`needs-info`）KVM 拔線偵測時間（ICE consent 逾時約 24～36 秒，推論）是否縮短，待現場 K1 實測。

## 後續

- 到現場依 `docs/FIELD_VERIFICATION.md` 執行 D1～D7、K1～K7、R1，並回填 #69。
- 第二輪評估未開票的候選：C5 CoreChannel worker 佇列、C6 指令描述表、C7 HID 對位點擊原語；C3 後續的兩套凍結判斷合併（見 #57 票「後續事項」）。
- 協定 1.1（輪次 UUID、ACK、持久化、14 天保留）仍未開始。

## 觀察

- 子 session 有時不依指示行事：兩個早期審查 session 未詢問即貼 comment；#59 審查 session 自行修正並合併。現行流程已明文授權貼 comment，但 merge 只由主 session 執行。
- 子 session 可能不採信主 session 轉達的跨 session 訊息（#57 session 曾暫停）；brief 內預先寫明流程可避免。
- 自動事件觸發的回合不能直接啟動新 session，只能提出啟動卡，需使用者點開。
