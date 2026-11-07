# 專案摘要 2026-11-07：1080p 現場辨識修正、pop-out 手動操控、DFU 搜尋範圍

接續 `PROJECT_SUMMARY_20261023.md`。10-24～11-05 之間合併的票（#126～#186：HMI 面板、心跳、連線失敗列、timing log、Core exe／localhost／autostart、綠色遮擋、協定訊息英文化等）見各 PR 與 `docs/retros/2026-11-06-issue185-protocol-english.md`。

結束時 GitHub main 與 Gitea `origin/main` 皆在 `8c0a2d9`（11-07 已補推 origin），之後只多本摘要與 retro 的文件提交。

## 1. 背景

週一（2026-11-09）客戶看線，重點是 BT 測試流程與 DFU 輸入條碼流程。現場螢幕 **1920×1080 @100%**。回報的問題：

- BT `monitor_start` 常回 `Log Solution not found within 3 s after Dock click; no shortcut sent`，畫面其實沒被遮。
- BT `start_test` 找得到視窗，但 `Start All not found (similarity 0.52)`。
- 希望在 KVM Log 的 pop-out 畫面直接操控目標電腦，不用開瀏覽器。

## 2. 完成的 PR

| PR | 內容 | 根因／做法 |
| --- | --- | --- |
| #187 | pop-out 可手動操控目標電腦 | Core 新增 `hid` 指令，UI `stream_view` 接 `input_sender`，分「監看／手動控制」模式 |
| #188 | BT 1080p 修正 | ① Start All 搜尋範圍原本只取視窗模板寬度（`found.width`），按鈕在模板外 → 改為視窗左上角延伸到畫面邊緣，找不到再全畫面，雙失敗回報較高分數 ② scale 1.0 下 H.264 讓 WAITING (217) 偏向 STOPPED (191) → 深層環在 `scale < 1.4` 且與角落票距離 ≤ 42 時跳過，不整格判 None ③ `monitor_start` 失敗訊息附 `(last reading: <reason>)` |
| #189 | DFU 搜尋範圍 | 不再依賴 `DFU_window.png`（底色會隨 ATE 模式變）→ 讀 `DFU/DFU_region.json` `{"x","y","width","height"}` 固定螢幕矩形；缺檔時第一次 DFU 動作自動建全螢幕預設；矩形小於 checkbox 模板回 `hid_failed` |

## 3. 解析度問題的結論

- **不是解析度本身**。BT Start All 是搜尋範圍錯；monitor_start 是 scale 1.0（測試治具是 Retina 2×，現場是 1×）下讀取器對 H.264 色偏太敏感。
- 審查端 `tools/codec_stress.py`（繁忙桌面，1920×1080，模擬編碼，不是 JetKVM 實機）：

| Tk scale | kbps | reliable | heartbeat readable |
| --- | ---: | ---: | ---: |
| 1× | 1500 | 0/45 | 0/45 |
| 1× | 4000 | 41/45 | 42/45 |
| 2× | 1500 | 45/45 | 45/45 |
| 2× | 4000 | 45/45 | 45/45 |

→ 現場 JetKVM 串流品質必須調最高，並看 `core_status.readable_ratio`。

## 4. 使用者決定

- 實作用 Sonnet，PR 由使用者以 codex sol 6.1 審查，通過才合併，合併後刪分支與工作樹。
- DFU 缺 `DFU_region.json` 時自動建全螢幕預設（2026-11-07 決定），已知限制：視窗外同圖樣元件可能被誤採納，填入實際矩形後消失。

## 5. 流程教訓

- macOS 上全量 pytest 會卡在 Tk 測試（CPU 100% 數小時）→ 只跑相關測試檔，CI 為準。
- `sync_with_base_branch` 不接受 http 的 `origin`，PR 在 `github` remote → 手動 `git fetch github main && git merge github/main`。
- `hid_session.Box` 可迭代不可索引，要 `tuple(box)[:2]`。

## 6. 待辦

- 週一前：產線電腦 `git pull`；設定 `DFU/DFU_region.json` 實際矩形；JetKVM 串流品質最高；1080p 重拍 BT `start_all` 與 DFU checkbox／輸入欄位／OK 模板；現場跑 BT `monitor_start → start_test`、DFU `monitor_start → input → start_test`；失敗收錯誤訊息的 last reading 與 `~/Documents/template/_captures/match_diagnostics/<DEVICE>/latest/`。
- #190：#188 後續（Windows CI 清單、`find_start_all`／`last_reason` 分支測試、真 H.264 回歸測試、`docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md:371` 同步）。
- #111（Windows 17 項既有測試失敗）優先於 #190。
- 仍開著：#166、#158、#154、#152～#149、#141、#138、#134、#131、#130、#129、#126、#125、#124、#69、#171。
