# PR #144 evidence: red-row KVM lamp follows the real connection

Generated with `shoot144.py` (in this folder) on Tk 8.6.16 (Xft, Noto Sans CJK TC) on a 3000x2000 Xvfb screen,
the same way as `docs/evidence/pr139`: 100% = Tk scaling 4/3 on 1920x1080, 200% = 8/3 on 2880x1800.
`before/` is `main` at 5684e9f; `after/` is this PR. Three rows: DFU-01 Frame frozen (KVM per file name),
DFU-02 Connected, DFU-03 Offline (grey).

- `red_row_kvm_offline_{100,200}pct_{en,zh}.png`: frozen row, KVM disconnected. Before: solid white dot. After: white 3 px hollow ring, no fill.
- `red_row_kvm_online_{100,200}pct_{en,zh}.png`: frozen row, KVM connected (unchanged: green dot, white ring).
- `red_row_kvm_offline_tooltip_{100,200}pct_{en,zh}.png`: the row tooltip. After adds `KVM: Disconnected` / `KVM：已斷線`.

Note: at 200% under Xvfb the tooltip is cut by the panel edge in both before and after; that is unchanged by this PR.
