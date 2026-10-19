# 07: 文件：PROTOCOL、LabVIEW 交接、流程

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/47（Parent #40）

**What to build:** PROTOCOL.md 依實作改寫並與 `docs/LABVIEW_HANDOFF.md` 一致，讓 LabVIEW 設計者能直接依文件建立 typedef 與流程。

**Blocked by:** 02（#42）monitor_start 修訂：準備中等待、異常立即回報、04（#44）本輪開始請求：monitor_not_ready 與 manual_verification_required、05（#45）check_start：測試開始確認

**Status:** ready-for-agent

- [ ] PROTOCOL.md：移除 `check`；新增 `check_start`／`check_finish`；`monitor_start` 的 `timeout_sec`；共用 `data` 欄位、`kind` 表、`state` 值、`active` 證據類型；新錯誤碼 `log_solution_review`；`monitor_not_ready`／`manual_verification_required` 規則；範例與 typedef 清單；三站呼叫順序
- [ ] `docs/LABVIEW_HANDOFF.md` 的 `kind` 表與錯誤碼處置和 PROTOCOL.md 一致；狀態列由「尚未實作」改為已實作
- [ ] README 與 DFU／FCT／BT workflow 文件改用新指令與順序
- [ ] PROTOCOL.md 第 10 節待辦更新
