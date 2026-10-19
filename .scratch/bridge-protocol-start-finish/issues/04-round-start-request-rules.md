# 04: 本輪開始請求：monitor_not_ready 與 manual_verification_required

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/44（Parent #40）

**What to build:** 依本輪開始請求判斷結束確認能否交付：沒要求開始就查 → `monitor_not_ready`；要求了開始卻沒見到本次監控就緒，畫面出現可交付結果（人工停止或準備逾時後確認）→ `manual_verification_required`，不交付、不自動重測。

**Blocked by:** 02（#42）monitor_start 修訂：準備中等待、異常立即回報、03（#43）以 check_finish 取代 check，改用共用回覆格式

**Status:** ready-for-agent

- [ ] 無本輪開始請求時 `check_finish` 回 `monitor_not_ready`，不取用畫面殘留結果
- [ ] 有請求但未見本次監控就緒、出現完成或人工停止完成（全部終局）→ `manual_verification_required`，`results = []`，不點 Dock、無任何 HID
- [ ] 有請求且已見本次監控就緒 → 正常判讀
- [ ] 交付（`taken`）或回 `manual_verification_required` 後清除請求；之後須重新 `monitor_start`
- [ ] socket 層測試覆蓋：從未 monitor_start、monitor_start 後直接人工停止（0011）、準備逾時後 REVIEW 確認變完成、正常流程
- [ ] 1～5 都修改 TCP 伺服器，依序進行（1 → 2 → 3 → 4 → 5），不要由不同 agent 並行。
- [ ] （2026-10-19 補充）交付後畫面簽章改變、沒有新一輪信號、請求已清除 → `monitor_not_ready`（不是 `already_taken`，也不交付）
- [ ] （2026-10-19 補充）DFU `input`：待命可輸入；完成或人工停止完成且本輪已交付（`taken`）或已回 `manual_verification_required` → 可輸入；完成或人工停止完成但尚未交付且未回 `manual_verification_required` → `log_solution_busy`；監控中、準備中、待確認 → `log_solution_busy`
- [ ] （2026-10-19 補充）測試覆蓋：準備中起點建立請求、`monitor_not_started` 後請求仍在、交付後重看、交付後簽章改變、人工停止完成交付前後的 `input`
