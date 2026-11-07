# 專案摘要 2026-11-08（承 2026-11-07）

## PR #194：Dock 點擊後移開滑鼠、等新影格、重試一次

- 問題：BT 點 Dock 後滑鼠停在圖示上，程式名稱提示蓋住 Start All；另外 `session.frame()` 可能拿到點擊前的舊影格。
- 做法：`HidSession` 新增 `park_pointer()`、`wait_fresh_frame()`、`match_with_retry()`、`focus_dock_fresh()`（Dock 點擊 → 停放 → 等新影格的唯一入口）。BT／DFU／monitor 都走它；BT 與 DFU 比對未命中時，不再點 Dock，移開滑鼠、等新影格後重比一次（key 加 `_retry`）。Start All／OK 點完也移開滑鼠。
- 設定：`~/Documents/template/COMMON/hid_settings.json`，`pointer_park: [x, y]`（預設螢幕上緣中間；開自動隱藏選單列時要改）、`fresh_frame_timeout_sec`（預設 2）。每個指令讀驗一次，在第一次 HID 前驗證，`null` 與錯型別回 `hid_failed`。
- 審查第一輪（codex）4 項修正：(1) 移開滑鼠為 best effort，失敗只記 `park_failed`，不把已成功的點擊報成失敗；(2) 設定先驗證；(3) 以停放時最新影格序號為基準，settle 期間到達的影格也算新影格；(4) `monitor_start` 的等新影格併入點擊起算的 confirm 期限（PROTOCOL §5.6 的 3 秒）。
- 教訓：Windows 的 `time.monotonic()` 解析度約 15 ms，測試中比較「收件時間 > 停放時間」要用明確差值，不能靠連續呼叫。
- 待現場驗證：提示是否真的不再蓋住 Start All；`pointer_park` 在現場的最佳位置。
