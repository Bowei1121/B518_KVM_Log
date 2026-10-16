# 06: 文件更新：PROTOCOL、三站流程、部署

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/27（Parent #21）

**What to build:** 更新文件以符合三站統一 Log Solution 判讀與新指令，讓 LabVIEW 依文件建立 typedef 與呼叫順序，部署人員依文件設定現場。

**Blocked by:** 03（#24）start_test 取代 button，DFU input 不再點 OK、04（#25）check 看不到 Log Solution 的恢復與 round_lost、05（#26）淘汰模板判讀結果程式與結果模板

**Status:** ready-for-agent

- [ ] PROTOCOL.md：新增 `monitor_start`、`start_test`，移除 `button`；錯誤碼加入 `log_solution_not_found`、`monitor_not_started`、`monitor_not_ready`、`log_solution_not_visible`、`round_lost`；更新指令支援表、範例、typedef 清單；協定版本維持 1.0
- [ ] PROTOCOL.md 記錄三站呼叫順序：DFU `input` → `monitor_start` → `start_test` → `check`；BT `monitor_start` → `start_test` → `check`；FCT 推入前訊號 → `monitor_start` → `check`
- [ ] DFU／FCT／BT workflow 文件改為 Log Solution 判讀與新順序
- [ ] 部署文件：ATE 視窗不可放大、不可遮住右側；FCT 推入前發訊號；Log Solution profile 通道數 = 設備清單；連結 Log Solution 1.2 建議文件
- [ ] README 模板章節更新（共用 Log Solution Dock 圖示、移除結果模板）
