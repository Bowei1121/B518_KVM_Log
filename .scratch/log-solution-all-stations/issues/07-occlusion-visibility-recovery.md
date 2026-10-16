# 07: 可見性恢復：部分遮住也觸發點 Dock、計時改排除法與 30 秒

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/31（Parent #21）

**Status:** ready-for-agent

## What to build

Log Solution 被 ATE 視窗部分遮住（例如只遮左側結果格、只遮結果帶）時，`check` 目前每張畫面都把可見性計時歸零，永遠不點 Dock，只能等到逾時。修正後：只要看不到 Log Solution（整個或部分被遮住、關閉、縮到 Dock），`check` 在約 35 秒內點 Dock 恢復判讀；3 次仍看不到約 105 秒回 `log_solution_not_visible`。合成畫面實測與決定見 `docs/PROJECT_SUMMARY_20261017.md`。

## Acceptance criteria

- [ ] 「Log Solution 不可見」用排除法判定：`kind = unknown` 且 `reason` 不屬於畫面／串流問題類（`no_frame`、`invalid_frame`、`stale_frame`、`missing_source_frame_time`、`out_of_order_source_frame`、`duplicate_or_out_of_order_frame`、`inspection_busy`、`inspection_timeout`）即算；日後新增的 unknown reason 預設也算
- [ ] 計時從第一次看不到起算；只有可靠畫面（standby／monitoring／review／complete）歸零；畫面／串流問題類不累計也不歸零；不同 reason 交替不歸零
- [ ] 觸發時間 30 秒（可注入常數）；點 Dock 後 5 秒重新判讀；同一 `check` 最多 3 次，恢復成功不重置次數
- [ ] `log_solution_not_visible` 的 `data.reason` 為最後一次判讀原因
- [ ] 可見性恢復時 Dock 上找不到 Log Solution 圖示 → 立即回 `log_solution_not_found`，不算一次恢復
- [ ] 點 Dock 後滑鼠移到中性位置（不點擊）；`monitor_start` 點 Dock 後亦同
- [ ] `inspect` 仍只回報、不點擊
- [ ] 測試輔助 `LogSolutionKVM` 新增 `cover_left(until_x, colour, y_range=None)`（colour 支援灰／白／黑／雜訊；y_range 可只遮結果帶）；點 Dock 成功清除遮擋，並可設定「點了也恢復不了」
- [ ] socket 層測試：只遮結果格（灰、黑）→ 點 Dock → 恢復判讀；只遮結果帶；恢復不了 → 3 次 → `log_solution_not_visible`；`inspect` 不點擊；串流問題類讀數夾在中間不歸零不累計；找不到 Dock 圖示 → `log_solution_not_found`；點擊後滑鼠移到中性位置
- [ ] 全套測試以本機權限通過

## Blocked by

None (can start immediately)
