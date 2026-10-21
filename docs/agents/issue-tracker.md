# Issue tracker: GitHub

Issues and specs for this repo live in GitHub Issues at `Bowei1121/B518_KVM_Log`. Use the `gh` CLI with `--repo Bowei1121/B518_KVM_Log` for tracker operations: `origin` is company Gitea, while the GitHub remote is `github`.

## Conventions

- Create: `gh issue create --repo Bowei1121/B518_KVM_Log --title "..." --body-file <file>`.
- Read: `gh issue view <number> --repo Bowei1121/B518_KVM_Log --comments`; fetch labels alongside the body and comments.
- List: `gh issue list --repo Bowei1121/B518_KVM_Log --state open --json number,title,body,labels,comments`, with appropriate state/label filters.
- Comment: `gh issue comment <number> --repo Bowei1121/B518_KVM_Log --body-file <file>`.
- Labels: `gh issue edit <number> --repo Bowei1121/B518_KVM_Log --add-label "..."` / `--remove-label "..."`.
- Close: `gh issue close <number> --repo Bowei1121/B518_KVM_Log` after recording the outcome.
- Write multiline issue/comment bodies to a temporary UTF-8 file with actual newlines, then use `--body-file`.

## Existing local tickets

`.scratch/jetkvm-relay-refactor/issues/` retains historical relay tickets. New issues use GitHub. A local ticket number is not automatically the same GitHub issue number; use an explicit file path or URL when referring to historical work. This setup does not migrate or publish existing tickets.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub issues and PRs share numbers. Resolve an ambiguous number with `gh pr view <number> --repo Bowei1121/B518_KVM_Log` and fall back to `gh issue view`.

## Skill operations

- Publish to the issue tracker: create a GitHub issue in the repo above.
- Fetch the relevant ticket: read the issue, labels and comments using the explicit repo.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, with Notes / Decisions-so-far / Fog.
- Child: link tickets as GitHub sub-issues; where unavailable, put them in the map task list and add `Part of #<map>` to the child. Use `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling` or `wayfinder:task`.
- Blocking: use native issue dependencies when supported. Otherwise add `Blocked by: #<n>` at the top. A ticket is unblocked when all blockers are closed. Native dependency operations require the numeric database issue id, not the displayed issue number.
- Frontier: map order, open, unblocked and unassigned children.
- Claim: assign the driving developer before work.
- Resolve: comment the outcome, close the child and append a concise linked decision to the map.

## 驗收勾選（2026-10-21 起）

Issue 的驗收條件（`- [ ]` 清單）是其他 session、其他電腦判斷進度的唯一依據，必須和實際審查結果一致。

- **誰勾**：審查 session（不是實作方）。每輪審查（含再審）結束、貼完 PR 審查 comment 後，到對應 issue 更新勾選。
- **勾什麼**：只勾本輪**已實際驗證通過**的項目（讀過程式／跑過測試或 mutation）。未通過、需修正、未驗證的保持 `- [ ]`。再審發現先前通過的項目退步，要改回 `- [ ]`。
- **使用者取消或改寫的條件**：保留原文，以 `~~刪除線~~` 標示並註明決定出處（comment 連結），再勾選。
- **現場驗證項目**：開發階段無法驗證的保持不勾，註明對應 `docs/FIELD_VERIFICATION.md` 編號。
- **每輪留 comment**：在 issue 留一則簡短 comment，寫明本輪結論（可合併／需修正）、勾了哪幾項、未勾項的原因，並附 PR 審查 comment 連結。
- **編輯方式**：`gh issue view N -R Bowei1121/B518_KVM_Log --json body --jq .body` 取原文，只把對應行的 `- [ ]` 改為 `- [x]`（文字一字不改），寫入暫存 UTF-8 檔後 `gh issue edit N -R Bowei1121/B518_KVM_Log --body-file <file>`。
- **合併時**：主 session merge 前確認 issue 驗收條件與最後一輪審查一致；有未勾項時，須在 issue 說明為何仍可合併（例如現場驗證、使用者同意延後）。
