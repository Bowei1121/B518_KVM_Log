# Log Solution 1.3 fixtures

- `ls6-heartbeat-state-00-en.png`: native macOS window-server capture of the Log Solution Tk window in state 0000 (STANDBY, heartbeat all white). Retina capture, 752×1672 image pixels for a 376×836 window, so about 2 image pixels per Tk unit. It is not a KVM frame.
- `window-bounds.json`: the same evidence run's control bounds, copied as-is.

Source: `B518 Log Solution/docs/refactoring/evidence/ls6-heartbeat/` in the Log Solution repo, commit `aff6b33` (contract 1.3 commit). Later Log Solution HEAD `959e706` leaves both files and `src/kvm_display_contract.py` unchanged since `aff6b33`.

Used only for geometry replay: the test reads the image at 2× as captured and at 1× after `INTER_AREA` halving. Do not use it as a remote KVM sampling reference; LS7 KVM pixel measurements are still pending (`docs/FIELD_VERIFICATION.md` L6).
