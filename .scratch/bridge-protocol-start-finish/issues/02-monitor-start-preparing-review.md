# 02: monitor_start 修訂：準備中等待、異常立即回報

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/42（Parent #40）

**What to build:** `monitor_start` 新增必填 `timeout_sec`（1–300），準備中持續等待監控就緒，遇 Log 異常待確認（REVIEW）立即回 `log_solution_review`；成功送出快捷鍵時建立該設備的本輪開始請求。

**Blocked by:** 01（#41）認得 Log 1.2 的六種整輪狀態

**Status:** ready-for-agent

- [ ] `timeout_sec` 必填，範圍 1–300，不合法回 `invalid_argument`
- [ ] 開始前為監控中 → `log_solution_busy`；為 REVIEW → `log_solution_review`；兩者皆不送快捷鍵
- [ ] 開始前為準備中 → 不送快捷鍵、直接等待監控就緒
- [ ] 送出快捷鍵後：準備中繼續等；REVIEW 立即 `log_solution_review`；`timeout_sec` 內未見監控就緒 → `monitor_not_started`
- [ ] 送出快捷鍵即為該設備建立本輪開始請求（供票 4 使用）
- [ ] socket 層測試以合成畫面依序呈現 準備中→監控中、準備中→REVIEW、一直準備中，驗證按鍵紀錄與錯誤碼
- [ ] 1～5 都修改 TCP 伺服器，依序進行（1 → 2 → 3 → 4 → 5），不要由不同 agent 並行。
- [ ] （2026-10-19 補充）開始前已是準備中時，不送快捷鍵，但要建立（或保留）本輪開始請求；同一次呼叫內看到監控中即視為已見本次監控就緒
- [ ] （2026-10-19 補充）回 `monitor_not_started` 時不清除本輪開始請求（只有 `taken` 或 `manual_verification_required` 會清除）
