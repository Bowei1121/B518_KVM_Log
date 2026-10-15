# 08: Monitor UI 改用設備 ID 並唯讀手動檢查

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/9（Parent #1）

**What to build:** Monitor UI 移除手動 IP 欄位，設備選擇改由 Core `core_status` 提供的設備清單。操作員的手動檢查不上鎖、不改變輪次狀態，不會阻擋或搶走 LabVIEW 的 `check` 結果。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 02（#3）協定 1.0：envelope 與請求驗證、04（#5）`check` 1.0：逐格結果、待確認立即回、格數核對

**Status:** ready-for-agent

- [ ] UI 無手動輸入 KVM IP 的欄位；設備下拉來自 `core_status`
- [ ] 快照、即時串流、手動檢查都以設備 ID 送出，符合協定 1.0
- [ ] 手動檢查不取得 Device Lock；LabVIEW 長輪詢 `check` 期間執行手動檢查不會造成 `device_busy`
- [ ] 手動檢查後 LabVIEW 的 `check` 仍能取得 taken（不出現 `already_taken`）
- [ ] UI client 測試覆蓋新請求格式與唯讀旗標
