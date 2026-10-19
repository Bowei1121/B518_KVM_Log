# 05: check_start：測試開始確認

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/45（Parent #40）

**What to build:** 新增 `check_start`：以連續兩張新鮮影格確認通道已開始測試，回 `kind = "started"` 與 `active[{slot, evidence}]`；已有完整可交付結果時直接回 `taken`。可重複呼叫、不消耗結果，套用本輪開始請求規則。

**Blocked by:** 04（#44）本輪開始請求：monitor_not_ready 與 manual_verification_required

**Status:** ready-for-agent

- [ ] `check_start` 必填 `timeout_sec`（0–3600），上鎖
- [ ] 活動：TESTING／COMPLETING → `evidence = "testing"`；未見測試中但有 PASS／FAIL 等有效結果 → `evidence = "result"`；NOTEST／TIMEOUT／STOPPED／WAITING 不算
- [ ] 通道須在連續兩張新鮮影格（序號前進、間隔 ≤ 1 秒）皆有活動才列入，取交集；中間 unknown 重新計算
- [ ] 已有完整可交付結果 → `taken`（或 `already_taken`）附完整結果，之後的 `check_finish` 回 `already_taken`
- [ ] 重複呼叫回最新 `active`，不影響之後 `check_finish` 的 `taken`
- [ ] 本輪開始請求規則與 `check_finish` 相同（`monitor_not_ready`、`manual_verification_required`）；REVIEW → `paused`；可見性恢復適用
- [ ] `inspect` 也填入 `active`（唯讀、不點擊）
- [ ] socket 層測試以逐張變化的合成畫面覆蓋兩影格交集、證據類型、非活動狀態、直接交付、重複呼叫
- [ ] 1～5 都修改 TCP 伺服器，依序進行（1 → 2 → 3 → 4 → 5），不要由不同 agent 並行。
