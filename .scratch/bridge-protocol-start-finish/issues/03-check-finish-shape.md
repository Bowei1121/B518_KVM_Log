# 03: 以 check_finish 取代 check，改用共用回覆格式

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/43（Parent #40）

**What to build:** 移除 `check`，新增 `check_finish`（測試結束確認），回覆改為 `check_start`／`check_finish`／`inspect` 共用的格式。第一次交付回 `taken`；同一輪之後可重看，回 `already_taken` 並附相同結果。人工停止完成以 `state = "manual_stop_complete"` 交付。

**Blocked by:** 01（#41）認得 Log 1.2 的六種整輪狀態

**Status:** ready-for-agent

- [ ] 送 `check` 回 `unknown_command`；`check_finish` 必填 `timeout_sec`（0–3600），長輪詢與逾時語意同舊 `check`
- [ ] `data` 固定為 `kind`、`state`、`reason`、`capacity`、`active`、`results`；本票 `active` 一律 `[]`
- [ ] `kind` 為 `taken`／`already_taken`／`waiting`／`paused`／`unknown`（`check_finish` 不回 `started`）
- [ ] 穩定簽章包含完成種類＋容量＋逐格結果；一般完成與人工停止畫面交替時重新計數
- [ ] 人工停止完成且全部終局 → `taken`、`state = "manual_stop_complete"`，`results` 原樣含 `STOPPED`；全部已有終局結果（無 STOPPED）仍為 manual_stop_complete
- [ ] 交付後同一輪（簽章相同、未開始新輪）再查 → `already_taken`，`results` 與第一次相同；看到監控中／準備中或新 `monitor_start` 後不再視為同一輪
- [ ] `inspect` 改用同一格式，不回 `taken`
- [ ] 既有 `check` 測試改寫為 `check_finish`；可見性恢復套用於 `check_finish`
- [ ] 1～5 都修改 TCP 伺服器，依序進行（1 → 2 → 3 → 4 → 5），不要由不同 agent 並行。
