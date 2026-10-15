# 02: 協定 1.0：envelope 與請求驗證

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/3（Parent #1）

**What to build:** 依 `docs/PROTOCOL.md` 1.0 統一請求驗證與回覆格式，讓 LabVIEW 能以固定 typedef 解析。新增 LabVIEW 用的 `status`，原完整內部狀態改名 `core_status` 僅供 Monitor UI。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 01（#2）設備清單：載入、驗證，並以設備 ID 取畫面

**Status:** ready-for-agent

- [ ] 請求驗證順序：JSON → 物件 → `protocol_version`（`1.0`）→ `req_id` 必填 → `cmd` → 欄位白名單 → 設備 ID → 站別支援 → 上鎖
- [ ] 錯誤碼：`invalid_json`、`invalid_request`、`unsupported_protocol_version`、`missing_req_id`、`missing_command`、`unknown_command`、`missing_device`、`unknown_device`、`unknown_field`、`forbidden_field`、`invalid_argument`、`unsupported_command`
- [ ] 每筆回覆固定含 `protocol_version`、`req_id`、`device`、`cmd`、`status`、`error`、`message`、`elapsed_sec`、`data`；`data` 欄位依指令固定，錯誤時以預設值出現，不出現 `null`
- [ ] `ping`（`device` 為空字串）回 `pong`、`core_version`
- [ ] `status` 回 `core_uptime_sec` 與每台設備 `device`、`station`、`connected`、`busy`、`frozen`、`channels`，不含 KVM IP
- [ ] 原 `status`／`stations` 改名 `core_status`；`shutdown` 保留但不列入 LabVIEW 文件
- [ ] socket 層測試覆蓋每個錯誤碼與每種回覆的完整欄位集合
- [ ] Core 不再自動補 `req_id`
