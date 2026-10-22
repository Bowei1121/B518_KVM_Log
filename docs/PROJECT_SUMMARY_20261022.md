# 專案摘要 2026-10-22：第三輪架構評估與收尾統整

統整 2026-10-20～10-21 整輪工作，作為長期查閱入口。細節見 `PROJECT_SUMMARY_20261020.md`、`PROJECT_SUMMARY_20261021.md`，以及各 issue 的「驗收核對」comment。結束時 main 在 `2c93715`，GitHub 與 Gitea 同步，全套約 800 passed。

## 1. 起點與方法

- 以 `/improve-codebase-architecture` 依 commit 熱點（`tcp_server.py` 最多）找出 6 個深化候選。使用者選 C1～C5，經 grilling Q1–Q48 定案後開票。
- 執行方式：
  - 實作：Haiku session；可獨立的票交給 Grok。
  - 審查：Opus session，結果貼成 PR comment，修正後必須再審。
  - 合併：審查判「可合併」後由主 session merge，並手動同步 Gitea `origin`（http，app 的同步工具不支援）。

## 2. 完成的票（依合併順序）

| 票 | 內容 | PR |
| --- | --- | --- |
| #71 | 刪死碼、唯一進入點 `core_daemon` | #80 |
| #72 | HidSession（找圖／點擊／HID 錯誤收斂、`FlowError`） | #79 |
| #73 | FrameStream（兩套凍結判斷合一，stream generation） | #85 |
| #74 | `input`／`start_test`／`monitor_start` 送 HID 前檢查凍結 | #86 |
| #75 | RoundCheck＋DeviceSlot（判斷順序同步可測） | #87 |
| #76 | CommandSpec 指令描述表 | #88 |
| #81 | HidSession 點擊前回報殘留 HID 錯誤 | #89 |
| #82 | 測試隔離 HOME、刪 `device_lock._owners` | #91 |
| #32 | 只支援 Log Solution 顯示契約 1.3 | #93 |
| #77 | Log Solution 心跳停止偵測 | #97 |
| #34 | 色表釘選（NOTEST `#f04bf1`） | #100 |
| #94 | `core_status` 回報 `frame_size`／`picture_scale`／`cell_px`／可讀率 | #99 |
| #95 | 非整數混色縮放定位，並修掉 main 既有的讀錯 | #101 |

工具：`tools/codec_stress.py`（PR #96）。它把契約 1.3 畫面經 H.264 壓縮後讀回來；只要有讀錯，結束碼就是 1，可當回歸檢查。

母票 #1、#21、#40 已結案。

## 3. 重要決策（使用者裁決）

- **Log Solution 心跳**（使用者提出）：
  - PTS 只能證明串流活著，偵測不到 Mac 或 App 當機，因此加了心跳。
  - 規格：2×2 黑白、16 態、每 0.5 秒 +1。
  - 判定：連續 3 張有效觀察圖案相同（間隔 ≥ 0.8 秒、≤ 3 秒）就回 `log_solution_heartbeat_stopped`；讀不到則視為不可見。
- **顯示契約 1.3**（Log Solution `aff6b33`）：
  - 位置：定位點 (0,2)／(320,2)、狀態格 (250,0)、心跳 (278,0)。
  - **橋接只支援 1.3，必須和 App 同時部署。**
- **解析度**：
  - 1 倍輸出（每小格 10 px）也要可讀，取消原本的 12 px 規則。
  - 縮放比例下限 0.70；非整數縮放已由 #95 修好。
- **殘留風險**（使用者接受）：
  - 白色視窗只蓋住心跳格時，畫面和心跳 state 0（全白）無法區分。
  - 淺色標題列加狀態色的兩段色視窗剛好蓋住第 1 格時，仍可能讀錯（測試網格中 971 次降到 420 次）。
- **`core_status` 診斷**只放在 Monitor UI 用的 `core_status`，LabVIEW 用的 `status` 不改。

## 4. 流程規則（本輪新增）

- **驗收勾選**：每輪審查（含再審）結束後，由審查 session 在 issue 勾選已驗證的項目、留下結論 comment。規則寫在 `docs/agents/issue-tracker.md`「驗收勾選」與 AGENTS.md。
- **語言**：AGENTS.md 最前面明訂回覆、PR、comment 一律使用繁體中文。
- **分工**：審查 session 只能貼 comment，不能自己修改或推送。#95 的審查曾在本機 commit 修正，被主 session 擋下。
- **Issue 回補**：2026-10-21 逐項核對 46 張 issue、300 多個勾選項，有證據的才打勾，每張附驗收 comment。

## 5. 經驗

- 審查用 mutation（故意改壞程式）驗證測試有效，抓到多次「測試寫了但測不到」，例如色表自比自、遮擋寬度漏測。
- 「讀錯」比「讀不到」嚴重。讀取器任何放寬都要用 codec_stress 和對抗案例證明 misread 為 0。
- 兩個 PR 平行修改同一檔案時，先合併一個，另一個再 rebase 並重新審查。
- 自動事件觸發的回合不能直接開 session，只能放卡片，要使用者點開。

## 6. 待辦（不在本輪範圍）

- **現場驗證**：照 `docs/FIELD_VERIFICATION.md` 逐項執行。最重要的是 L1／L2：實際 KVM 每小格像素與 HDMI 輸出解析度，可從 `core_status` 的 `frame_size`、`cell_px` 讀取。
- **#13**：LabVIEW 改版（人工），須依最新 PROTOCOL／LABVIEW_HANDOFF 實作。
- **#69**：等現場 K1 數據。
- **#77 未勾的一項**：還缺「心跳被遮時 check 點 Dock」的 socket 測試，以及 App 實際擷取的 replay 素材。
- **Log Solution 端**：HANDOFF 第 13～14 節要同步回 Log Solution 主文件。
- **本機啟動**：`~/Documents/template/devices.json` 不存在，Core 會拒絕啟動，可用 Monitor UI 的設定頁建立。主資料夾 checkout 停在舊的 `8f4e9d7`，需要更新到最新 main。

## HMI 改善系列（2026-10-21～22）

使用者檢視 Monitor UI 後提出 9 點，經 grilling Q1–Q21 定案，拆 3 張依序做（皆改 `ui_app.py`，不可並行），Haiku 實作、Opus 審查。

| 票 | 內容 | PR | 審查輪數 |
| --- | --- | --- | --- |
| #105 | 強制淺色（含 Mac `NSRequiresAquaSystemAppearance`）、機台 4 色燈號（FROZEN 閃爍）、服務單燈、Log 錯紅警橘、模板按鈕合併、短錯誤訊息＋點擊捲到 Log、畫面／Core Log 每日一檔留 30 天 | #108 | 3 |
| #106 | 中英雙語（預設英文、🌐 即時切換、偏好存 `%APPDATA%`／`~/Library/Application Support`）、圖示按鈕（純圖示放大、不用 VS16）、白話 tooltip、Log 檔每行 `HH:MM:SS \| EN \| 中文`、`summary.json` `note` 中英物件 | #109 | 2 |
| #107 | Windows DPI awareness（shcore→user32 fallback、tk scaling）、GitHub Actions Windows 打包＋import smoke test＋frozen exe `--self-test` | #110 | 2 |

- 決策：Log 不做「自動互補色」，根因是 Log 只寫死 fg 未設 bg，強制淺色即解；匹配結果圖上維持英文（`cv2.putText` 不支援中文）。
- 測試環境教訓：`.venv` 缺 Pillow 會讓外觀測試 skip、掩蓋深色模式缺陷（#108 第 2 輪才抓到）→ 審查要求 0 skipped。背景 pytest 須加 `faulthandler_timeout`，孤兒 pytest 會佔 port 讓 `test_monitor_start.py` 卡死。Tk 測試異常結束會讓 macOS 跳「重新打開 Python 視窗」提示（`defaults write org.python.python ApplePersistenceIgnoreState -bool YES` 可關）。
- 2026-10-22 Windows 11 實機（FIELD_VERIFICATION H1）：DPI、emoji、淺色、雙語通過；版面問題（預設視窗過小擠掉 Log、服務燈號被切、按鈕字截斷、設備設定視窗欄位被切、勾選框高 DPI 變形）待開票修正。
