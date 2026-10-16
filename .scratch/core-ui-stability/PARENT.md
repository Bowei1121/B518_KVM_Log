# Core 與 Monitor UI 穩定性修正（架構候選 3～5）

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/17

## Problem

2026-10-14 架構評估留下三個候選，2026-10-16 對 PR #16（`1a7e8c8`）唯讀查證後確認仍存在：

1. Frame Freeze Guard 不區分 KVM 重連後的新串流，重連後誤報 `frame_frozen`。
2. KVM Connection Pool 只在取得連線時更新使用時間，`check` 長輪詢超過閒置上限（預設 600 秒）時連線被淘汰關閉，接著多半誤報 `frame_frozen`。
3. Monitor UI 所有功能共用一條 socket 與一把鎖：長時間手動檢查期間狀態、快照、串流、設定頁全部卡住；狀態輪詢執行緒會堆積；關閉視窗會卡住。

1、2 只會誤報設備異常使產線暫停，不會誤判產品結果（ADR-0005 防呆保留）。

## Scope

三張子票各自獨立，可並行；1、2 優先（產線長時間 `check` 會遇到）。保留 ADR-0001 heartbeat／閒置淘汰、ADR-0004 程序解耦、ADR-0005 凍結防呆。不改協定 1.0 的欄位。

查證紀錄：`docs/PROJECT_SUMMARY_20261016.md`「第 6～8 項查證」。
