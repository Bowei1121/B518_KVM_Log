# Domain Docs

## Before exploring

This repo uses single-context. Read root `CONTEXT.md` and ADRs in `docs/adr/` relevant to the area under exploration.

If a future `CONTEXT-MAP.md` exists, follow its pointers to relevant context glossaries and context-scoped ADRs. If documents are absent, proceed silently; domain-modeling creates them when actual terms or decisions crystallize.

## Layout

- `CONTEXT.md`: shared domain vocabulary.
- `docs/adr/`: accepted architecture decisions.
- `host-app/`: first-party host code.
- `third_party/jetkvm/`: pinned upstream submodule, not a separate first-party context.

## Vocabulary and scope

Use domain terms exactly as defined in `CONTEXT.md`. The host observes HDMI frames, interprets visual results and operates HID; local ATE log reading belongs to the separate project described in `docs/PROJECT_SUMMARY.md`.

If a required concept is missing, record the gap for domain-modeling rather than silently introducing a competing name.

## ADR conflicts

Mark proposals that contradict an accepted ADR, identify the ADR and explain the observed friction that warrants revisiting it. Preserve accepted decisions until a new decision is recorded.
