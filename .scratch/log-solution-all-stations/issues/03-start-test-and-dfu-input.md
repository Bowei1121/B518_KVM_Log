# 03: start_test 取代 button，DFU input 不再點 OK

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/24（Parent #21）

**What to build:** 新增 `start_test`：DFU 點 OK、BT 點 Start All、FCT 回 `unsupported_command`；Log Solution 非 Monitoring 時拒絕。移除 `button`。DFU `input` 只校正 checkbox 與輸入 SN，不點 OK、不送快捷鍵，開始監控改由 `monitor_start` 負責。

**Blocked by:** 02（#23）共用 Log Solution Dock 圖示模板與 monitor_start

**Status:** ready-for-agent

- [ ] `start_test` 需上鎖、無專屬欄位；Log Solution 非 Monitoring 回 `monitor_not_ready` 且不做 HID
- [ ] DFU：前景化 DFU 視窗並點 OK，`data.action=ok`；BT：沿用 Start All 流程，`data.action=start_all`
- [ ] FCT 回 `unsupported_command`；定位或 HID 失敗回 `hid_failed`
- [ ] `button` 指令移除，送出回 `unknown_command`
- [ ] DFU `input` 不點 OK、不送 Cmd+Shift+M；其餘驗證與 `log_solution_busy` 規則不變，回 `data.entered`
- [ ] socket 層測試以合成畫面驗證三站點擊／按鍵紀錄與錯誤碼；既有 BT Start All 操作測試改經 `start_test`
- [ ] 1、2、3、4 都修改 TCP 伺服器，避免由不同 agent 同時進行。
