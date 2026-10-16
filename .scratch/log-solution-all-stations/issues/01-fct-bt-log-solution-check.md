# 01: FCT／BT 的 check／inspect 改用 Log Solution 判讀

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/22（Parent #21）

**What to build:** 三站的 `check` 與 `inspect` 走同一條 Log Solution 顯示契約判讀路徑（與 DFU 相同的 RoundFrameGate 防呆）。完成後 FCT／BT 能以 `kind=taken` 交付逐格結果，站別不再影響判讀。尚未安裝 Log Solution 的 ATE 回 `unknown`，絕不產生產品結果。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] FCT、BT 的 `check` 與 DFU 行為一致：先見 Monitoring 才接受 Complete、兩張新鮮一致完整畫面才 `taken`、`capacity` ≠ 通道數回 `capacity_mismatch`、取用後回 `already_taken`、Review 立即回 `paused`、凍結回 `frame_frozen`
- [ ] 支援 1～20 通道與兩排色帶（第二排為通道 11～N）
- [ ] FCT、BT 的 `inspect` 也讀 Log Solution 色帶，不上鎖、不消耗本輪結果
- [ ] 畫面上找不到 Log Solution 時回 `kind=unknown`，不得為產品結果
- [ ] `check`／`inspect` 不再呼叫模板判讀結果路徑（程式刪除由票 05 處理）
- [ ] socket 層測試以合成 Log Solution 畫面覆蓋 FCT、BT 的上述情況
- [ ] 1、2、3、4 都修改 TCP 伺服器，避免由不同 agent 同時進行。
