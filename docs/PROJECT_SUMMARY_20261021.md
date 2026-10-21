# 專案摘要 2026-10-21

接續 `PROJECT_SUMMARY_20261020.md`。本檔記錄換日時的進度，供後續查閱。

## 前一日結論（2026-10-20）

- **第三輪架構評估**：6 個候選，C1～C5 grilling（Q1–Q33），開票 #71～#77。重構票全部完成並合併：
  - #71 清理（PR #80）、#72 HidSession（PR #79），由 Grok 實作、Claude 複審。
  - #73 FrameStream（PR #85）、#74 HID 前凍結檢查（PR #86）、#75 RoundCheck＋DeviceSlot（PR #87）、#76 CommandSpec（PR #88），由 Haiku 實作、Opus 審查，每張都經過「需修正 → 修正 → 再審可合併」。
- **後續票**：#81 HidSession 清錯吞錯（PR #89，Grok 實作、Claude 直接審查）、#82 測試寫入真實 `~/Documents/template`（PR #91）皆已合併。
- **Log Solution 心跳**（使用者提出）：PTS 只證明 JetKVM 串流活著，偵測不到 Mac／App 當機。
  - 定為 16 態黑白計數方塊，每 0.5 秒 +1。
  - 連續 3 張有效同圖 → `log_solution_heartbeat_stopped`。有效觀察須相隔 ≥ 0.8 秒、相隔 > 3 秒或有無效觀察則清空序列；讀不到 → 不可見。
  - 不鎖定；計時區不讀。
- **顯示契約 1.3**（Log Solution `aff6b33`）：定位 (0,2)／(320,2)、狀態 (250,0)、心跳 (278,0)。
  - 使用者決定**橋接只支援 1.3**，須與 App 同時部署。
  - 1 倍輸出（每小格 10 px）也要可讀（取消 12 px 規則）。
  - #32（PR #93）已合併，讀取器只認 1.3 並提供心跳四格。
- main 在 `eab0906`，全套約 710 passed、34 subtests；GitHub 與 Gitea 同步。

## 開發流程（沿用）

- 實作：獨立 session，Haiku 5.5、effort high；Grok 負責可獨立的票。
- 審查：獨立 session，Opus 5.5、effort medium，結果貼 PR comment。修正後必須再審，「可合併」才 merge。
- merge 只由主 session 執行，並手動同步 Gitea `origin` 的 main。

## 本日

- **解析度風險評估**：新增 `tools/codec_stress.py`。它把契約 1.3 畫面經 libx264（zerolatency）壓縮再解碼讀回，所有情境 misread 為 0。結果（模擬，未實機驗證）：
  - 1080p、Tk 1 倍：1.5 Mbps 時可讀率約 90～98%；400 kbps 約 33%。
  - 畫面縮成 720p，或經過非整數混色縮放（macOS 縮放模式）：完全讀不到。
- 2026-10-20 回覆中「macOS 縮放模式 100% 可讀」是模擬寫錯（先放大再縮小，等於沒縮放），已更正。
- 新票：
  - **#94** `core_status` 回報每台設備的 `frame_size`、`picture_scale`、`cell_px`、`last_reading`、`readable_ratio`（只改 `core_status` 與 Monitor UI，不改 LabVIEW 的 `status`）。
  - **#95** 非整數混色縮放下定位點判讀失敗。實驗放寬內嵌白格容差後多數情境可讀，但 macOS 縮放 2880 寬仍因方向取樣失敗。
- 進行中：#77 心跳偵測（Haiku session 實作中）。

## Issue 驗收勾選（2026-10-21）

- 使用者在其他電腦看 issue 時，因為勾選框都沒打勾、也沒有驗收 comment，無法判斷哪些已經做完。這次逐項核對了 46 張 issue，共 300 多個勾選項。依據是合併的 PR、審查 comment，以及 main `7d560c3` 的程式與測試；本機全套測試 710 passed、34 subtests。有證據的項目才打勾，每張 issue 另補一則「驗收核對」comment，列出各項的狀態和依據。
- 所有已關閉的 issue 都已全部勾選。#32 的「< 12 px 判讀不到」子條款，依使用者裁決以刪除線標示取消。
- 仍開著、保留未勾的：
  - #13：LabVIEW 改版，屬人工工作。
  - #34：顏色釘選，**實際未實作**。NOTEST 仍為 `#f04bf0`，三項釘選測試都沒有做；只有 STALLED 一項本來就成立，已勾。
  - #69：等現場 K1 數據。
  - #77：審查中。
  - #94、#95：尚未開工。
- 小缺口（不影響已勾項目）：
  - #36、#43：沒有「0011＋非終局格」的專屬測試。
  - #45：沒有 check_start 專屬的 manual_verification_required 與 Dock 恢復 socket 測試。
  - #57：PR #65 新增的 `_OneFrameKVM` 沒有走共用 FakeKVM。

## 待辦

- #77 PR → 審查 → 合併。
- #94、#95 建議在 #77 合併後開工（都碰讀取器或 DeviceSlot）。
- #34 顏色釘選尚未實作，可交 Grok。
- 現場：`docs/FIELD_VERIFICATION.md` D／K／R／L 各項，特別是 L1、L2（實際 KVM 像素與 HDMI 輸出解析度）；#69 拔線偵測時間。
- 協定 1.1（輪次 UUID、ACK、持久化）仍未開始。

## 本日後段（2026-10-21）

| 票 | PR | 實作／審查 | 重點 | 合併後 main |
| --- | --- | --- | --- | --- |
| #77 心跳停止偵測 | #97 | Haiku／Opus | 心跳格被遮、標記可讀時 HID 仍送出 → 規則移入 `read_snapshot`；驗收 3／4（replay 素材與「心跳被遮時 check 點 Dock」的 socket 測試待補） | `02ce202` |
| #34 色表釘選 | #100 | Grok／Claude | NOTEST 改 `#f04bf1`；hex 釘選、色距、表外顏色測試；5 個 mutation 全抓到 | `9565c89` |
| #94 core_status 診斷 | #99 | Haiku／Opus | `frame_size`、`picture_scale`、`cell_px`、`last_reading`、`readable_ratio`；經 3 輪審查補足記錄點測試 | `92170b5` |
| #95 非整數縮放定位 | #101 | Haiku／Opus | 白洞容差與中心原點；方向取樣移入黑環；新增第 1 格上方背景檢查，修掉 main 既有讀錯（左定位被遮＋假方塊）；單色遮擋讀錯 803→0；經 3 輪審查 | `9f143d5` |

- **流程新規則**：每輪審查（含再審）結束後，審查 session 要在 issue 勾選已驗證的驗收條件，並留結論 comment（`docs/agents/issue-tracker.md`「驗收勾選」、AGENTS.md）。另外 AGENTS.md 最前面明訂回覆、PR 與 comment 一律使用繁體中文。
- **使用者裁決**：兩段色遮擋（淺色標題列＋狀態色剛好蓋住第 1 格）的殘留讀錯風險予以接受，不另開票（測試網格下 971→420）。
- **審查越權**：#95 審查 session 曾在本機 commit 修正、打算推上 PR 分支，被主 session 擋下；修法只以 comment 提供，實際修正仍由實作方完成。
- 仍開著的 issue：#69（現場 K1）、#13（LabVIEW，人工）、#1／#21／#40（Spec 母票，子票皆已完成，待使用者決定是否關閉）。
