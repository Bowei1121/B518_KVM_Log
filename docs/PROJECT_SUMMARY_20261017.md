# B518 JetKVM — 專案摘要（2026-10-17）

日期：2026-10-17（Asia/Taipei）
前一日詳細紀錄：`docs/PROJECT_SUMMARY_20261016.md`。

## 2026-10-16 結果

- FCT／BT 判讀方式 grilling（A1–A23）定案：三站統一以 Log Solution 判讀；新增 `monitor_start`、`start_test`（取代 `button`）；DFU `input` 只填 SN；看不到 Log Solution 時點 Dock 恢復；監控中轉 standby 回 `round_lost`。
- Log Solution 原始碼查證：30 秒為單邊下限；人工停止後標記為 standby（含 `STOPPED` 輪次交付不到）；視窗未設最上層。給 Log Solution 的修改建議：`docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md`。
- spec #21 與票 #22～#27 由「Log Solution 三站統一判讀實作」session 完成，PR #30 已合併 main（`c8c4ba0`）。穩定性修正 #17～#20 由 PR #29 合併。
- PR #16（spec #1）、#28（10-16 文件）已合併並同步 Gitea。#13（LabVIEW）仍延後，已留言提醒以 #21 後的 PROTOCOL 為準。
- 文件分支 `docs/log-solution-occlusion-design`（`53350e9`）記錄「右側不可遮、左側可遮並以 Dock 恢復」，尚未開 PR；該分支所在 worktree 屬於已完成的 session（無未提交修改），本日改從新分支 `docs/occlusion-recovery-decisions` 接手。

## grill-with-docs：Log Solution 被遮住時的可見性恢復

問題：現行 `check` 只在兩種定位失敗 reason 下計時點 Dock，且任何其他讀數都會歸零計時。

合成畫面實測（子代理，4 狀態 × 4／20 通道 × 5 顏色 × 3 形狀，兩種縮放一致）：

- 只遮到結果格（左定位方塊左側，約左側 22%）：`result_cell_unknown_N`（黑色遮擋與 `non_contiguous_capacity` 交替）→ 每張都歸零計時，**永遠不點 Dock**。
- 遮過左定位方塊（x≈82 單位）至整個遮住、關閉、縮到 Dock：`locators_missing_or_ambiguous` → 會點 Dock。
- 只遮結果帶：`result_cell_unknown_1`／`capacity_not_visible` → 永遠不點 Dock。
- 點 Dock：Log Solution 沒有 reopen 處理，走 macOS 預設（帶到前面）；恢復流程不送按鍵，不會開始／停止輪次。找不到 Dock 圖示時 `bring_log_solution_forward` 回 `False`，但 `check` 忽略並照算一次恢復。點完滑鼠停在 Dock 上。

已決定（A1–A3、A6–A10）：

- A1／A7：「看不到 Log Solution」用排除法：任何 `unknown` 都算（定位失敗類＋部分遮擋類＋日後新增的 reason）；畫面／串流問題類（`no_frame`、`invalid_frame`、`stale_frame`、`missing_source_frame_time`、`out_of_order_source_frame`、`duplicate_or_out_of_order_frame`、`inspection_busy`、`inspection_timeout`）不累計也不歸零；可靠畫面（standby／monitoring／review／complete）歸零。錯誤的 `data.reason` 帶最後一次判讀原因。
- A2：計時從第一次看不到起算，只有可靠畫面才歸零，不同 reason 交替不歸零。
- A3：同一 `check` 最多點 3 次 Dock，恢復成功也不重置次數。
- A6：觸發時間由 60 秒改為 **30 秒**（約 35 秒內點 Dock 並恢復；3 次失敗約 105 秒回 `log_solution_not_visible`）。
- A8：`check` 期間找不到 Dock 圖示 → 立即回 `log_solution_not_found`，不算一次恢復；屬嚴重異常，需停線檢查。
- A9：點 Dock 後滑鼠移到中性位置（不點擊），`monitor_start` 亦同。
- A10：測試輔助新增 `cover_left(until_x, colour, y_range)`，點 Dock 成功清除遮擋並可設定恢復失敗。使用者將把 Log Solution 的左定位方塊（「KVM RESULT」字樣右側那個）移到色帶最左側上方，使左右兩個定位方塊涵蓋整條色帶；定位方塊被遮住即點 Dock，3 次仍未出現回警示。
- A11：開修正票掛在 spec #21 下；文件分支內容一併更新；CONTEXT.md 新增「Log Solution 不可見」。

## 第 3 輪（A12–A14）與實作

- A12：定位方塊移動併入 1.2；橋接過渡期同時支援 1.1 與新位置（以兩定位方塊距離判斷），部署先更新橋接再更新 Log Solution。
- A13：Log Solution 拿掉「KVM RESULT」字樣，再把左定位方塊移到色帶第 1 格正上方；座標由 Log Solution 開發者依五條限制條件決定（`docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md` C6）。
- A14：分兩張票並先做票 A。票 A = #31（`ready-for-agent`, `bug`），票 B = #32（`needs-info`，等 Log Solution 定座標；本次新建 `needs-info` 標籤）；皆為 #21 的 sub-issue，本機副本 `.scratch/log-solution-all-stations/issues/07-*.md`、`08-*.md`。
- CONTEXT.md 新增「Log Solution 不可見」；PROTOCOL.md 5.4、錯誤碼表、第 10 節與 DEPLOYMENT.md 第 4 節改為本次定案。
- 票 A 已實作（分支 `docs/occlusion-recovery-decisions`，commit `9340ad0`）：排除法判定不可見、只有可靠畫面歸零、30 秒、找不到 Dock 圖示回 `log_solution_not_found`、點 Dock 後滑鼠移到畫面頂端中央（避開 Dock 與 macOS 熱角）。新測試先在舊程式上失敗（18 failed），修正後全套 454 passed、30 subtests passed。
