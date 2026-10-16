# 02: KVM Connection Pool 不淘汰使用中的連線

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/19（Parent #17）

**What to build:** 連線池只在取得連線時更新 `last_used_at`，heartbeat 與使用期間都不更新。`check`／`inspect` 長輪詢（`timeout_sec` 上限 3600）超過閒置上限且沒有其他請求時，連線會在使用中被淘汰關閉，畫面停止更新並誤報 `frame_frozen`。修正後：連線在被指令使用期間不會被淘汰；指令結束後才開始計算閒置時間。

前置：以 PR #16（spec #1 協定 1.0）合併後的 main 為基礎。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 連線池提供取得／歸還（或 context manager）語意，記錄每條連線的使用中數量
- [ ] 閒置淘汰跳過使用中的連線；歸還時更新 `last_used_at`
- [ ] TCP 指令分派在整個 handler 執行期間持有連線，結束（含例外與客戶端斷線）時一定歸還
- [ ] heartbeat 與閒置淘汰行為對未使用中的連線不變（ADR-0001）
- [ ] 測試：使用中連線的 `last_used_at` 早於閒置上限，執行淘汰後仍未關閉；歸還後再超過上限才被淘汰；例外路徑也會歸還
