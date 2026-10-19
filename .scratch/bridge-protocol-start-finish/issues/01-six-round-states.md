# 01: 認得 Log 1.2 的六種整輪狀態

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/41（Parent #40）

**What to build:** 橋接判讀 Log Solution 1.2 的六種兩黑兩白狀態圖樣：待命 1001、準備中 0101、監控中 1100、待確認 1010、完成 0110、人工停止完成 0011；其他圖樣、缺格、低對比為 unknown。準備中算可靠畫面；其他指令在準備中時不得往下走。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 判讀認得 0101（`state = "preparing"`）與 0011（`state = "manual_stop_complete"`）；全黑、全白、一黑三白等為 unknown
- [ ] 合成畫面測試輔助可繪製六種圖樣
- [ ] `inspect` 回報 `preparing`／`manual_stop_complete`
- [ ] 準備中為可靠讀數：重置可見性計時、不點 Dock
- [ ] 準備中時 `input` 回 `log_solution_busy`（不做 HID）、`start_test` 回 `monitor_not_ready`（不點擊）
- [ ] 現有 `check` 在準備中回 `waiting`（`check` 移除由票 3 處理）
- [ ] 判讀層與 socket 層測試覆蓋上述各項
- [ ] 1～5 都修改 TCP 伺服器，依序進行（1 → 2 → 3 → 4 → 5），不要由不同 agent 並行。
