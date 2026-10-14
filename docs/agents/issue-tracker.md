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
