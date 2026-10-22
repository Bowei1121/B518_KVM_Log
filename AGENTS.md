# Project instructions

**回覆請使用「繁體中文」。** 適用於所有對使用者的回覆與回報（含子 session、子代理交回的報告），以及 PR 標題與內文、PR／issue comment、專案摘要等文件。程式碼、識別字、指令與錯誤碼維持原文。

每次執行修改完程式之後，都要自動提交到 git repo 與 remote repo。
換日後的第一次對話需要將之前的對話摘要與討論結果存在一份專案摘要，方便未來查閱與減少 token 使用。
每次大任務（例如一整輪架構評估、一個 spec 的所有子票）結束後，依 `docs/retros/TEMPLATE.md` 寫一份復盤紀錄到 `docs/retros/<日期>-<任務>.md`（Notion 可直接貼上的 Markdown），提交後把檔案交給使用者，方便日後復盤。

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues at `Bowei1121/B518_KVM_Log`. Before tracker work, read `docs/agents/issue-tracker.md`.

審查（含再審）結束後，審查 session 必須依 `docs/agents/issue-tracker.md`「驗收勾選」把已驗證通過的驗收條件打勾、未通過的保持不勾，並在 issue 留下本輪結論 comment。

### Triage labels

Use the five default triage role labels. Before triage or label changes, read `docs/agents/triage-labels.md`.

### Domain docs

This repo uses single-context: root `CONTEXT.md` and `docs/adr/`. Before codebase exploration or architecture work, read `docs/agents/domain.md`.
