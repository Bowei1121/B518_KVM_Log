# 專案摘要 2026-11-08：#193 合併、ATE PR #145 跨 repo 審查

接續 `PROJECT_SUMMARY_20261107.md`。

## 1. 11-07 摘要之後合併到本 repo 的內容

| PR／提交 | 內容 |
| --- | --- |
| #191 | check 期間可手動 HID（復盤：`docs/retros/2026-11-07-pr191-check期間手動HID.md`） |
| #192 | 比對診斷圖歷史歸檔（復盤：`docs/retros/2026-11-07-比對診斷圖歷史歸檔.md`） |
| #193（`df1b5f3`） | JetKVM 影格改用 BT.601 解碼；替 Log Solution 讀取存下標註診斷圖，加上 LS 定位配對過程與最高 4 倍放大圖 |

GitHub `main` 在 `df1b5f3`。Gitea `origin/main` 原本停在 `2e12906`（少 #193），這次隨本摘要一起補推。

## 2. 跨 repo 審查：`Bowei1121/B518_205_207_ATE` PR #145

PR 內容：Log Solution 設定頁在 macOS 改用 `osascript choose folder ... invisibles true`，讓 KVM 只用滑鼠也能選到 `.` 開頭的隱藏資料夾；工站啟動時路徑錯誤會顯示「專案 / 機型：欄位（路徑值或未設定）」。

審查格式沿用本 repo PR #178：範圍、驗證、必須修改、建議，最後附結論表（審查版本／結論／阻擋／驗證），並依 ATE repo 的 `skills/issue-pr-review-loop/references/workflow.md`。

| 輪次 | head | 結論 | 重點 |
| --- | --- | --- | --- |
| 初審（Review 5813381017） | `66a1128` | 需修改 | ① `subprocess.run` 同步等 osascript，Tk 主迴圈停住，KVM 心跳凍結，所有 `_schedule_ui` 計時器停擺，輪次中也能開設定；② darwin 分支的測試全被 patch 掉 |
| 再審（Review 5813485685） | `bf198d0` | 審查通過 | 改成 `Popen` 加 `_schedule_ui` 100 ms 輪詢；關設定窗、root destroy、`close()` 時 terminate；防連點；去掉結尾 `/`；新測試鎖住心跳持續跳動、成功、取消、兩種 fallback、關窗取消 |

仍未處理的非阻擋建議：
- osascript 對話框可能在 App 後面（加 `activate`／`default location`），連點時沒有任何提示。
- `stderr=DEVNULL`，fallback 時沒有記錄。
- 錯誤訊息沒列出原因，`repr()` 會露出引號。
- 測試名稱與實際驗證的內容不符；optional 欄位的錯誤訊息沒有測試。
- PR 沒有 `Refs #`，修正者也沒有逐項回覆。

限制：審查 session 的本機 pytest 被權限擋下，只能靠讀程式加上 CI `test-and-package` pass；osascript 實際顯示與實機 DFU／KVM 都還沒驗證。合併需使用者授權，並以 `--match-head-commit bf198d0…` 綁定版本。

## 3. 待辦

- ATE #145：等使用者授權合併；合併後實機確認對話框是否在前景，以及選取期間 JetKVM 端的心跳是否持續跳動。
- 本 repo 延續 11-07 的待辦：11-09 客戶看線（BT 測試、DFU 條碼），JetKVM 串流品質調最高，觀察 `core_status.readable_ratio`。



## PR #194：Dock 點擊後移開滑鼠、等新影格、重試一次

- 問題：BT 點 Dock 後滑鼠停在圖示上，程式名稱提示蓋住 Start All；另外 `session.frame()` 可能拿到點擊前的舊影格。
- 做法：`HidSession` 新增 `park_pointer()`、`wait_fresh_frame()`、`match_with_retry()`、`focus_dock_fresh()`（Dock 點擊 → 停放 → 等新影格的唯一入口）。BT／DFU／monitor 都走它；BT 與 DFU 比對未命中時，不再點 Dock，移開滑鼠、等新影格後重比一次（key 加 `_retry`）。Start All／OK 點完也移開滑鼠。
- 設定：`~/Documents/template/COMMON/hid_settings.json`，`pointer_park: [x, y]`（預設螢幕上緣中間；開自動隱藏選單列時要改）、`fresh_frame_timeout_sec`（預設 2）。每個指令讀驗一次，在第一次 HID 前驗證，`null` 與錯型別回 `hid_failed`。
- 審查第一輪（codex）4 項修正：(1) 移開滑鼠為 best effort，失敗只記 `park_failed`，不把已成功的點擊報成失敗；(2) 設定先驗證；(3) 以停放時最新影格序號為基準，settle 期間到達的影格也算新影格；(4) `monitor_start` 的等新影格併入點擊起算的 confirm 期限（PROTOCOL §5.6 的 3 秒）。
- 教訓：Windows 的 `time.monotonic()` 解析度約 15 ms，測試中比較「收件時間 > 停放時間」要用明確差值，不能靠連續呼叫。
- 待現場驗證：提示是否真的不再蓋住 Start All；`pointer_park` 在現場的最佳位置。
