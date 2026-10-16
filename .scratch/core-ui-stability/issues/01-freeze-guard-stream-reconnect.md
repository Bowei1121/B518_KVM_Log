# 01: Frame Freeze Guard 區分 KVM 重連的新串流

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/18（Parent #17）

**What to build:** KVM 斷線重連後，新串流的畫面時間戳從低值重新開始。Frame Freeze Guard 目前不記串流編號，把這種情況當成時間戳停滯，立即回 `frame_frozen`，直到新時間戳超過舊值為止（可能數小時）。修正後：同一串流的時間戳停滯或倒退仍判凍結；換成新串流時，該設備的凍結狀態重新起算。

前置：以 PR #16（spec #1 協定 1.0）合併後的 main 為基礎。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 凍結檢查的每台設備狀態記錄串流編號；串流編號改變時重置該設備狀態，以新串流第一張畫面為新基準
- [ ] 同一串流時間戳不前進達門檻仍回 `frame_frozen`（ADR-0005 行為不變）
- [ ] 所有呼叫凍結檢查的路徑（`check`、`inspect` 長輪詢，以及模板判讀內的檢查）都傳入串流編號
- [ ] 測試：舊串流 PTS 100 → 新串流 PTS 1 且持續前進，不回 `frame_frozen`；同串流 PTS 停滯仍回 `frame_frozen`；兩台設備狀態互不影響
- [ ] 測試以既有 socket 層測試與假 KVM（提供 `latest_frame` 5 欄位）撰寫
