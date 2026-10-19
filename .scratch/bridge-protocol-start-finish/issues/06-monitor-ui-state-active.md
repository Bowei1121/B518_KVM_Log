# 06: Monitor UI 顯示狀態與已開始通道

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/46（Parent #40）

**What to build:** Monitor UI 的手動檢查改用新回覆格式，顯示 Log 狀態（含準備中、人工停止完成）與已開始的通道及證據類型。

**Blocked by:** 05（#45）check_start：測試開始確認

**Status:** ready-for-agent

- [ ] 手動檢查使用 `inspect` 新格式，顯示 `state` 的中文名稱
- [ ] 列出已開始通道與證據（測試中／已有結果）
- [ ] UI client 測試覆蓋新欄位解析；Tk 畫面本身不做自動化測試
