# 03: 所有模組只認設備 ID

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/4（Parent #1）

**What to build:** Device Lock、Frame Freeze Guard（含模板判讀流程內的凍結檢查）、DFU RoundFrameGate、匹配診斷目錄全部改以設備 ID 為 key；KVM Connection Pool 一律使用設備清單查得的 IP。完成後三站可同時執行指令不再互相 BUSY，多台同站設備不再共用凍結或輪次狀態。注意：本票與「協定 1.0」票都大幅修改 TCP 伺服器，避免兩者同時由不同 agent 進行。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 01（#2）設備清單：載入、驗證，並以設備 ID 取畫面

**Status:** ready-for-agent

- [ ] `DFU-01`、`FCT-01`、`BT-01` 同時執行上鎖指令皆成功；同設備第二個上鎖指令回 `device_busy`
- [ ] 兩台 BT（例如 `BT-01`、`BT-02`）的凍結檢查狀態互不影響
- [ ] DFU RoundFrameGate 以設備 ID 區分
- [ ] 匹配診斷輸出目錄以設備 ID 命名
- [ ] 移除 `snapshot` 依「站別或設備號任一符合」猜設定的邏輯
- [ ] socket 層測試覆蓋跨站不互鎖、同站多台狀態隔離
