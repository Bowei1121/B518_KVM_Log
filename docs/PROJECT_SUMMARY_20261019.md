# B518 JetKVM — 專案摘要（2026-10-19）

日期：2026-10-19（Asia/Taipei）
前一次詳細紀錄：`docs/PROJECT_SUMMARY_20261017.md`。

## 2026-10-17 結果

- grill-with-docs「Log Solution 被遮住時的可見性恢復」定案並實作：排除法判定「Log Solution 不可見」、只有可靠畫面歸零、30 秒觸發點 Dock、同一 `check` 最多 3 次、找不到 Dock 圖示回 `log_solution_not_found`、點 Dock 後滑鼠移到畫面頂端中央。
- 票 #31 由 PR #33 合併 main（`53bfc55`），全套 454 passed；#32（支援 1.2 左定位方塊新位置）為 `needs-info`，等 Log Solution 依 C6 定座標。
- `docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md` 新增 C6：拿掉「KVM RESULT」字樣、左定位方塊移到色帶第 1 格正上方、五條限制條件、先更新橋接再更新 Log Solution。
- 主資料夾 checkout 由 `49dfea6` 快轉到最新 main；`AGENTS.md` 新增「回覆語言請使用繁體中文」（`14df02d`），兩個 remote 同步。
- 已提供 #32 的 Haiku／implement-spec prompt（含開工前檢查：標籤須為 `ready-for-agent`、Log Solution 契約須已寫入新座標）。

## Log Solution 端的回應（唯讀查證 Log Solution repo `fa2b571`）

- Log Solution 於 2026-10-17 讀過 1.2 建議並排序：P0 C1＋C2、P1 C4／C3／C6＋C5。尚未核可實作；契約文件仍為 1.1（左定位方塊仍在 x=82）。
- 其分析指出：C1 須同步處理稽核重建；C6 第一格在 x=0，須驗證定位點左緣與外側留白；移動定位點不能涵蓋所有只遮色帶的形狀，consumer 須保留格位可讀性驗證（與本 repo 排除法一致）。

## 本日

- 使用者要求閱讀 Log Solution `docs/refactoring/KVM_DISPLAY_CONTRACT.md`（1.1），評估其狀態辨識格的意義能否結合到橋接判讀流程。

## 契約 1.1 與橋接判讀對照

- 幾何（左右定位方塊、排數標記、狀態標記、結果格）、黑白門檻（0–80／176–255）、四種狀態圖樣，橋接 `round_frame_consumer.py` 與契約 1.1 完全一致。
- 狀態意義對應：Standby → 本輪未見監控中為等待、已見監控中為 `round_lost`；Monitoring → 記為已開始、等待；Review → 立即 `paused`；Complete → 兩張相同完整畫面且格數 = 通道數才 `taken`。契約要求接收端自行驗證前後一致與遮擋，由兩張畫面一致與 #31 排除法涵蓋。
- 缺口：契約沒有格子顏色表；Log Solution `STATUS_COLOURS` 中 `STALLED` 與 `TIMEOUT` 同為 `#ff9900`，且 `STALLED` 未被使用；NOTEST 兩邊差一個色階；契約「App 與上位機必須一起更新」與 A12 過渡期雙支援衝突。

## 決定

- Q1：1.2 建議文件新增 **C7**：格子顏色表寫進契約、移除未使用的 `STALLED`、NOTEST 以 `#f04bf1` 為準、容許誤差為 RGB 歐氏距離 ≤ 20（任兩色須 > 40；WAITING 與 STOPPED 約 45，餘裕小）。
- Q2：橋接釘住顏色並開票 **#34**（`ready-for-agent`，#21 的 sub-issue；本機副本 `.scratch/log-solution-all-stations/issues/09-pin-status-colours.md`）。
- Q3：C5 改為「1.2 起上位機先支援新舊兩種畫面，再更新 App」。
