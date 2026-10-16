# 03: Monitor UI 通訊分流：長時間檢查不再卡住其他功能

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/20（Parent #17）

**What to build:** Monitor UI 的狀態輪詢、快照、即時串流、手動檢查與設定頁共用一個 Core 連線與一把鎖。手動檢查等待結果期間（最長 `timeout_sec`＋4 秒）其他功能全部卡住；狀態輪詢每 1.5 秒開新執行緒會堆積；關閉視窗或切換連線在 Tk 主執行緒上等鎖而凍結。修正後：長時間檢查不影響狀態顯示、快照與設定頁；關閉視窗立即回應。

前置：以 PR #16（spec #1 協定 1.0）合併後的 main 為基礎。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 狀態、串流／快照、長時間檢查（`inspect`）各自使用獨立的 Core 連線（ADR-0006 每連線依序處理）
- [ ] 狀態輪詢上一筆尚未回來時不再發出新的；不再無上限建立執行緒
- [ ] 每個請求以 `req_id` 核對回覆，不符時丟棄並重新連線
- [ ] 關閉視窗與切換連線不在 Tk 主執行緒上等待鎖；進行中的請求被取消或其 socket 被關閉
- [ ] 設定頁的「測試連線」與「重啟 Core」不受進行中的手動檢查阻擋
- [ ] Tk 畫面本身不做自動化測試；連線分流、輪詢不重疊、`req_id` 核對、關閉取消放在可測試的 client 層，沿用既有 UI client 測試寫法
