# 04: `check` 1.0：逐格結果、待確認立即回、格數核對

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/5（Parent #1）

**What to build:** `check` 依 Log Solution 顯示契約 1.1 回覆 1.0 結構，LabVIEW 只依逐格結果做產品處置。長輪詢遇待確認立即通知；畫面格數必須等於設定的測試通道數。FCT／BT 過渡期仍用模板判讀，但輸出映射到同一結構。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 02（#3）協定 1.0：envelope 與請求驗證、03（#4）所有模組只認設備 ID

**Status:** ready-for-agent

- [ ] `data` 含 `kind`（taken／waiting／paused／already_taken／unknown）、`state`、`reason`、`capacity`、`results[]{slot, status}`；移除總結果欄位
- [ ] `timeout_sec` 必填：0 讀一次立即回；>0 長輪詢，上限 3600，超出回 `invalid_argument`
- [ ] 長輪詢於 taken、paused、already_taken 或錯誤時立即回；逾時回 `status=timeout`、`error=timeout`，`data` 帶最後觀察狀態
- [ ] `STOPPED` 視為終局狀態、原樣回傳；`COMPLETING` 視為等待
- [ ] taken 時 `capacity` ≠ 設備通道數回 `capacity_mismatch`，不交付結果
- [ ] `unknown` 不得表示為產品結果
- [ ] 長輪詢間隔 0.5 秒
- [ ] FCT／BT 模板判讀結果映射為同一 `data` 結構，不得在未看到結果時產生 taken
- [ ] socket 層測試以假 KVM 提供合成 Log Solution 畫面覆蓋上述各情況
