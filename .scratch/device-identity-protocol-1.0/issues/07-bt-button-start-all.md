# 07: BT `button` start_all 註冊為正式指令

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/8（Parent #1）

**What to build:** 將既有 BT Start All 流程註冊為 Core 正式指令，LabVIEW 以 `{"cmd":"button","action":"start_all"}` 啟動 BT 測試。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 02（#3）協定 1.0：envelope 與請求驗證

**Status:** ready-for-agent

- [ ] `action` 只接受 `start_all`，其他回 `invalid_argument`
- [ ] 成功回 `data.action = start_all`；HID／定位失敗回 `hid_failed`
- [ ] 非 BT 設備回 `unsupported_command`
- [ ] socket 層測試以假 KVM 覆蓋成功、失敗、站別不支援
