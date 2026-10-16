# 02: 共用 Log Solution Dock 圖示模板與 monitor_start

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/23（Parent #21）

**What to build:** 新增三站共用的 `monitor_start`：從 Dock 喚起 Log Solution、確認視窗在畫面上，再送 Cmd+Shift+M 並確認進入監控中。新增不分站別的 Log Solution Dock 圖示模板，創建 Pattern 可建立。擴充測試用合成 ATE 畫面以支援後續票。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 模板目錄新增不分站別的 `log_solution_dock_icon`，創建 Pattern 可建立與讀取
- [ ] `monitor_start` 三站可用、需上鎖、無專屬欄位；Log Solution 為 Monitoring 或 Review 時回 `log_solution_busy` 且不做 HID
- [ ] 找不到 Dock 圖示，或點擊後數秒內畫面上找不到 Log Solution，回 `log_solution_not_found`，不送快捷鍵
- [ ] 送 Cmd+Shift+M 後 10 秒內見 Monitoring 即成功，`data` 含 `state=monitoring` 與 `capacity`；否則 `monitor_not_started`
- [ ] 成功後該設備的輪次判讀視為已看過 Monitoring（後續 `check` 可直接取用本輪 Complete）
- [ ] 可從 Standby 或 Complete 開始；等待時間為可注入常數
- [ ] 合成 ATE 畫面測試輔助：繪製 Dock 上的 Log Solution 圖示、記錄點擊與按鍵、收到 Cmd+Shift+M 由 Standby／Complete 轉 Monitoring、可設定 Log Solution 被遮住或未開啟且點 Dock 後恢復
- [ ] socket 層測試覆蓋成功與每個錯誤碼、按鍵與點擊順序
- [ ] 1、2、3、4 都修改 TCP 伺服器，避免由不同 agent 同時進行。
