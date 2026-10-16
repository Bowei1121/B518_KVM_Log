# B518 Log Solution 修改建議：KVM 顯示契約 1.2

日期：2026-10-16（Asia/Taipei）
對象：B518 Log Solution 開發者（請依本文件在 Log Solution 專案自行開票）
提出方：B518 JetKVM Log 橋接程式
依據：Log Solution `main` `9cad82f`（VERSION 0.1.0）原始碼唯讀查證；`docs/refactoring/KVM_DISPLAY_CONTRACT.md`（1.1）
狀態：建議，尚未實作

---

## 1. 目標

1. **人工停止的輪次也要交付結果**：作業員按停止監控後，橋接程式要能取得本輪結果（含 `STOPPED` 格），讓 LabVIEW 將這批產品判不良、交作業員重工。
2. **需要讀結果時，色帶一定看得到**：開始監控、待確認、整輪結束時，Log Solution 視窗要在最前面，不被 ATE 測試程式擋住。
3. **FCT 先監控、後開測的時序可以正常運作**：FCT 治具推入前就開始監控，等待治具推入的時間不會被誤判為 `NOTEST`。

畫面幾何、定位方塊、狀態圖樣、排數標記、色帶顏色都**不變**，所以橋接程式的判讀不需要改。

## 2. 現況與問題

| # | 現況 | 位置 | 問題 |
| --- | --- | --- | --- |
| P1 | 人工停止後輪次為 `RoundState.STOPPED`；`state_for_round_snapshot` 只在 `COMPLETED and result_available` 時回 Complete，其他狀態一律 Standby | `src/kvm_display_contract.py` `state_for_round_snapshot`；契約文件 Standby 含 "manual stop" | 橋接看到 Standby 會解除本輪的 armed 狀態並回「等待中」，**含 `STOPPED` 的輪次永遠不會被取用**，LabVIEW 只會等到逾時 |
| P2 | `result_available` 只在 `state == COMPLETED` 時為真 | `src/monitoring_round.py` 建立 `RoundSnapshot` 處 | 同 P1 |
| P3 | `RoundCoordinator.stop()` 在 `monitor is None`（尚未建立 monitor，例如準備中）時，只把輪次設成 `STOPPED`，**不會**把非終局的格子改成 `STOPPED` | `src/monitoring_round.py` `stop()`；`BaseMonitor.stop` 只在 monitor 存在時改格子（`src/log_monitoring.py`） | 即使 P1 改了，色帶仍可能顯示 `WAITING`，橋接判定「結果未完整」而不取用 |
| P4 | 視窗沒有設定最上層；只在單格事件 `result` 且狀態是 PASS／FAIL／NOTEST 時呼叫 `_bring_dashboard_to_front` | `src/b518_log_solution.py` `_handle_event`、`_bring_dashboard_to_front` | 開始監控、待確認、`TIMEOUT`、`STOPPED`、整輪完成時都不會拉到前面；色帶可能一直被 ATE 視窗蓋住 |
| P5 | `_bring_dashboard_to_front` 會 `focus_force()` | 同上 | 搶走 ATE 程式的鍵盤焦點；新增的觸發時機若也搶焦點，可能干擾 ATE 操作 |
| P6 | 開始監控後，某格在 `start` 期限（預設 30 秒）內來源檔案沒有變動，就被鎖成 `NOTEST`，之後結果被忽略 | `src/monitoring_round.py` start deadline；`src/machine_profiles.py` 預設 `start: 30` | FCT 改成「治具推入前先監控」後，訊號到治具推入、再到第一次寫檔的時間若超過 30 秒，整批會被誤判 `NOTEST` |

## 3. 建議修改

### C1 人工停止 → Complete（解決 P1、P2）

規則：**人工停止且所有格子都是終局狀態、沒有待確認事項時，顯示 Complete，並視為結果可取用。**

```text
state_for_round_snapshot(snapshot)
  none                                         → Standby
  awaiting review / pending conflict / 未確認警報 → Review      # 優先序不變
  COMPLETED and result_available               → Complete
  STOPPED and completion_reason == manual_stop
      and result_available                     → Complete    # 新增
  RUNNING                                      → Monitoring
  其他（未開始、啟動失敗）                        → Standby
```

- `result_available`：`COMPLETED` 或「`STOPPED` 且 `completion_reason == manual_stop` 且 `collection_stopped` 且每格皆為終局狀態」時為真。
- 優先序不變：停止時仍有未處理的衝突或未確認的警報，就維持 Review，等人員處理完才變成 Complete。
- 啟動失敗或從未開始的輪次仍為 Standby。
- 稽核紀錄保留 `completion_reason = manual_stop` 與原始 `STOPPED` 狀態，不改寫成 FAIL。產品處置由 LabVIEW 依「非 PASS 即不良」處理。

### C2 停止時一定把非終局格子設為 STOPPED（解決 P3）

`RoundCoordinator.stop()` 在 `monitor is None` 的分支，也要把所有非終局格子設成 `STOPPED`，行為和 `BaseMonitor.stop` 一致。
- 結果：人工停止後，色帶每一格都是終局狀態（PASS／FAIL／NOTEST／TIMEOUT／STOPPED）。

### C3 視窗拉到前面的時機（解決 P4、P5）

新增以下觸發時機，呼叫「拉到前面但**不搶焦點**」的版本（`deiconify()`＋`lift()`，不呼叫 `focus_force()`）：

| 時機 | 原因 |
| --- | --- |
| 輪次進入 `RUNNING`（監控開始） | 橋接要確認「監控中」才讓 LabVIEW 開測 |
| 進入 Review（衝突、警報、`AWAITING_REVIEW`） | 橋接要立即通知 LabVIEW 暫停 |
| 單格結果 `TIMEOUT`、`STOPPED` | 補齊現有只處理 PASS／FAIL／NOTEST 的缺口 |
| 輪次結束（`COMPLETED`、人工停止後的 Complete） | 橋接要讀完整結果 |

- 現有 PASS／FAIL／NOTEST 的 `focus_force()` 行為是否保留，由 Log Solution 決定。建議也改成不搶焦點，避免干擾 ATE 程式。
- 不設定永久最上層，維持「畫面右側、不常駐最上層」的設計。部署 SOP 另外規定 ATE 視窗不可放大、不可蓋住右側。

### C4 FCT 的 start 期限可設定且加長（解決 P6）

- FCT（Atlas）profile 的 `start` 期限，依現場「收到訊號 → 治具推入 → 第一次寫入 `records.csv`」的實測時間設定，建議先設 **120 秒**再依實測調整。
- `start` 已經可以依 profile 設定（`_timeout_limit("start", …)`），只需要調整 FCT profile 的值，並在部署文件記錄實測依據。
- 因為監控會在治具推入前開始，歸檔資料夾時間戳「≥ 開始監控 − 30 秒」的門檻自然成立，不需要修改。

### C5 契約文件升為 1.2

更新 `docs/refactoring/KVM_DISPLAY_CONTRACT.md`：
- 標題與版本改為 1.2。
- Standby 的說明移除「manual stop」；Complete 的說明加上「人工停止且全部格子為終局狀態、無待確認事項」。
- 幾何、圖樣、顏色、排數標記不變，並註明「1.2 沒有新增畫面上的版本標記；與 1.1 consumer 相容」。

## 4. 相容性

| Log Solution | 橋接程式 | 結果 |
| --- | --- | --- |
| 1.1（現況） | 現況／新版 | 人工停止的輪次不交付，LabVIEW 等到逾時（安全，不會誤判） |
| 1.2 | 現況／新版 | 人工停止的輪次以 Complete 交付，`results` 含 `STOPPED`；橋接判讀程式不需要改 |

兩邊可以分開部署，不需要同時更新。

## 5. 驗收建議（Log Solution 專案）

1. `state_for_round_snapshot` 單元測試：
   - 人工停止、全部終局、無衝突 → Complete。
   - 人工停止但有未處理衝突或未確認警報 → Review。
   - 啟動失敗或未開始 → Standby。
   - `COMPLETED` → Complete（行為不變）。
2. `stop()` 在 `monitor is None` 時，所有非終局格子變成 `STOPPED`，`result_available` 為真。
3. 視窗前景：開始監控、進入 Review、`TIMEOUT`／`STOPPED` 結果、輪次結束時都會呼叫不搶焦點的前景函式（以假 root 驗證呼叫）。
4. FCT profile `start` 期限設為 120 秒時：監控開始後 100 秒才出現第一筆檔案變動，該格不會被鎖成 `NOTEST`。
5. 以 `tools/smoke_state_marker_app.py` 產生「人工停止 → Complete」的實際 Tk 擷取，交給橋接程式用既有 frame replay 工具驗證 `check` 回 `kind=taken`、`results` 含 `STOPPED`。

## 6. 橋接程式與部署的配合事項（供參考，不需 Log Solution 處理）

- 橋接新增三站共用的 `monitor_start`：先確認 Dock 上有 Log Solution 圖示並點擊，確認畫面上看得到 Log Solution，再送 Cmd+Shift+M，等待標記變成 Monitoring。
- 橋接 `check` 長輪詢期間，如果一段時間都找不到 Log Solution，會點 Dock 圖示嘗試拉回前面；仍然找不到才回錯誤給 LabVIEW。
- FCT 改為「治具推入前就發訊號給 LabVIEW」，LabVIEW 先 `monitor_start` 再讓治具推入。這需要和設備端協調。
- 部署 SOP：ATE 測試程式不可放大、不可蓋住畫面右側；Log Solution 各機台 profile 的通道數必須等於橋接設備清單的測試通道數。
