# 10: 支援 Log Solution 1.2 人工停止第五種圖樣 0011

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/36（Parent #21）

**Status:** needs-info

## What to build

> **2026-10-19 暫停（needs-info）**：HANDOFF 9.5 已確認把 `check` 拆成 `check_start`／`check_finish`，並新增 PREPARING（0101）與啟動失敗的 REVIEW。本票以單一 `check` 撰寫，需等橋接協定修訂（grill-with-docs）定案後併入新 spec 重寫，暫勿實作。

Log Solution 1.2 人工停止時改顯示第五種狀態圖樣 MANUAL_STOP_COMPLETE（上排白白、下排黑黑，0011；2026-10-19 使用者確認，見 `docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md`）。現行橋接不認得 0011，會判為 unknown，最後回 `log_solution_not_visible`，人工停止的輪次交付不到 LabVIEW。修正後：已見本次 Monitoring 的輪次在 0011 時交付全部結果並明確回報人工停止；未見本次 Monitoring 時不交付，回報需人工核對。必須在 Log Solution 1.2 部署前完成。

**協定細節（2026-10-19 使用者確認）：**
- 交付時 `data.kind = "taken"`、`data.state = "manual_stop_complete"`（`state` 新增一個值，不新增欄位，typedef 不變）；一般完成仍為 `state = "complete"`。
- 未見本次 Monitoring 就看到 0011：`status = "error"`、`error = "manual_verification_required"`，`data.kind = "unknown"`、`results = []`。

## Acceptance criteria

- [ ] 判讀認得 0011 為 MANUAL_STOP_COMPLETE；HANDOFF 定義的六種圖樣以外（例如全黑、全白、一黑三白）、缺格、低對比仍為 unknown（0101 已定為 PREPARING，不是 unknown）
- [ ] 已見本次 Monitoring（含 `monitor_start` 確認）、全部格位終局（PASS／FAIL／NOTEST／TIMEOUT／STOPPED）、格數 = 通道數、兩張相同畫面 → `taken`，`state = "manual_stop_complete"`，`results` 原樣含 `STOPPED`
- [ ] 每格都已有終局結果、沒有任何 STOPPED 格，但圖樣是 0011 → 仍回 `state = "manual_stop_complete"`
- [ ] 穩定確認的簽章包含「完成種類＋容量＋各格結果」：COMPLETE 與 MANUAL_STOP_COMPLETE 的畫面交替時重新計數，不得混用
- [ ] 未見本次 Monitoring 就看到 0011 → 回需人工核對的錯誤，不交付、不點 Dock、不自動重測
- [ ] 取用後再讀 → `already_taken`；0011 有非終局格 → 等待，不交付
- [ ] Review 仍優先：0011 之前若為 Review，照舊立即 `paused`
- [ ] `inspect` 讀 0011 回報狀態，不上鎖、不消耗本輪結果
- [ ] 合成畫面（`render_log_solution` 支援 0011）與 socket 層測試覆蓋上述各項；全套測試以本機權限通過
- [ ] PROTOCOL.md 更新 `state` 列舉值、錯誤碼表、5.4 人工停止段落、第 10 節

## Blocked by

None (can start immediately)。不依賴 #32（左定位方塊幾何），可先用 1.1 幾何實作與測試。
