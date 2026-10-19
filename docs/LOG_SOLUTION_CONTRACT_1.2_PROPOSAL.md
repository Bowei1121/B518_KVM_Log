# B518 Log Solution 修改建議：KVM 顯示契約 1.2

日期：2026-10-16（Asia/Taipei），2026-10-17 補充 C6，2026-10-19 補充 C7、修訂 C5，並改寫 C1 為第五種圖樣（以 `LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md` 為準）
對象：B518 Log Solution 開發者（請依本文件在 Log Solution 專案自行開票）
提出方：B518 JetKVM Log 橋接程式
依據：Log Solution `main` `9cad82f`（VERSION 0.1.0）原始碼唯讀查證；`docs/refactoring/KVM_DISPLAY_CONTRACT.md`（1.1）
狀態：建議，尚未實作

---

## 1. 目標

1. **人工停止的輪次也要交付結果**：作業員按停止監控後，橋接程式要能取得本輪結果（含 `STOPPED` 格），讓 LabVIEW 將這批產品判不良、交作業員重工。
2. **需要讀結果時，色帶一定看得到**：開始監控、待確認、整輪結束時，Log Solution 視窗要在最前面，不被 ATE 測試程式擋住。
3. **FCT 先監控、後開測的時序可以正常運作**：FCT 治具推入前就開始監控，等待治具推入的時間不會被誤判為 `NOTEST`。
4. **色帶左側被遮住時一定偵測得到**：左定位方塊移到色帶最左側上方，左右兩個定位方塊涵蓋整條色帶；任何從左側蓋到色帶的視窗都會先蓋住左定位方塊。
5. **格子顏色有唯一的書面來源**：每種格子狀態的顏色寫進契約，雙方都以契約為準，不再各自抄程式裡的值。

排數標記不變。**C1 新增第五種狀態圖樣（0011）、C6 改變左定位方塊的位置**，橋接程式都需要配合更新（見第 4 節）；C7 只把現有顏色寫進契約、移除未使用的 `STALLED`，並統一 NOTEST 的值；C2～C5 不影響橋接判讀。

## 2. 現況與問題

| # | 現況 | 位置 | 問題 |
| --- | --- | --- | --- |
| P1 | 人工停止後輪次為 `RoundState.STOPPED`；`state_for_round_snapshot` 只在 `COMPLETED and result_available` 時回 Complete，其他狀態一律 Standby | `src/kvm_display_contract.py` `state_for_round_snapshot`；契約文件 Standby 含 "manual stop" | 橋接看到 Standby 會解除本輪的 armed 狀態並回「等待中」，**含 `STOPPED` 的輪次永遠不會被取用**，LabVIEW 只會等到逾時 |
| P2 | `result_available` 只在 `state == COMPLETED` 時為真 | `src/monitoring_round.py` 建立 `RoundSnapshot` 處 | 同 P1 |
| P3 | `RoundCoordinator.stop()` 在 `monitor is None`（尚未建立 monitor，例如準備中）時，只把輪次設成 `STOPPED`，**不會**把非終局的格子改成 `STOPPED` | `src/monitoring_round.py` `stop()`；`BaseMonitor.stop` 只在 monitor 存在時改格子（`src/log_monitoring.py`） | 即使 P1 改了，色帶仍可能顯示 `WAITING`，橋接判定「結果未完整」而不取用 |
| P4 | 視窗沒有設定最上層；只在單格事件 `result` 且狀態是 PASS／FAIL／NOTEST 時呼叫 `_bring_dashboard_to_front` | `src/b518_log_solution.py` `_handle_event`、`_bring_dashboard_to_front` | 開始監控、待確認、`TIMEOUT`、`STOPPED`、整輪完成時都不會拉到前面；色帶可能一直被 ATE 視窗蓋住 |
| P5 | `_bring_dashboard_to_front` 會 `focus_force()` | 同上 | 搶走 ATE 程式的鍵盤焦點；新增的觸發時機若也搶焦點，可能干擾 ATE 操作 |
| P7 | 左定位方塊在 x=82（「KVM RESULT」字樣右側），色帶第 1～3 格在它左側 | `docs/refactoring/KVM_DISPLAY_CONTRACT.md` 幾何表 | ATE 視窗從左側只蓋住第 1～3 格時，定位方塊仍看得到，畫面判成「某格看不清」而不是「被遮住」；橋接已改為兩者都觸發恢復，但定位方塊涵蓋整條色帶更直接 |
| P8 | 格子顏色只寫在 `STATUS_COLOURS`，契約文件沒有顏色表；`STALLED` 與 `TIMEOUT` 都是 `#ff9900`，而 `STALLED` 除了顏色表以外沒有任何程式使用 | `src/b518_log_solution.py` `STATUS_COLOURS` | 橋接只能自己抄一份顏色；`STALLED` 一旦啟用，橋接會把它判成已逾時的最終結果。NOTEST 兩邊也差一個色階（`#f04bf1` 與橋接的 `#f04bf0`） |
| P6 | 開始監控後，某格在 `start` 期限（預設 30 秒）內來源檔案沒有變動，就被鎖成 `NOTEST`，之後結果被忽略 | `src/monitoring_round.py` start deadline；`src/machine_profiles.py` 預設 `start: 30` | FCT 改成「治具推入前先監控」後，訊號到治具推入、再到第一次寫檔的時間若超過 30 秒，整批會被誤判 `NOTEST` |

## 3. 建議修改

### C1 人工停止 → 第五種圖樣 MANUAL_STOP_COMPLETE（解決 P1、P2）

> **2026-10-19 修訂**：原建議「人工停止沿用 Complete 圖樣」已由使用者改為**新增第五種圖樣**，細節以 `docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md`（交接文件，兩專案同步副本）為準。本節只摘要，兩者不一致時以交接文件為準。

規則：**人工停止、所有格子都是終局狀態、沒有待確認事項時，顯示 MANUAL_STOP_COMPLETE，並視為結果可取用。** 一般完成仍顯示 COMPLETE，兩者必須可以分辨，橋接要明確回報「人工停止」。

| 狀態 | 上排 | 下排 | 位元（左上、右上、左下、右下；1＝黑） |
| --- | --- | --- | --- |
| STANDBY | B W | W B | 1001 |
| MONITORING | B B | W W | 1100 |
| REVIEW | B W | B W | 1010 |
| COMPLETE | W B | B W | 0110 |
| **MANUAL_STOP_COMPLETE** | **W W** | **B B** | **0011（新增，2026-10-19 使用者確認）** |

```text
state_for_round_snapshot(snapshot)
  none                                          → Standby
  awaiting review / pending conflict / 未確認警報 → Review                  # 優先序不變
  COMPLETED and result_available                → Complete
  STOPPED and completion_reason == manual_stop
      and result_available                      → ManualStopComplete      # 新增
  RUNNING                                       → Monitoring
  其他（未開始、啟動失敗）                         → Standby
```

- `result_available`：`COMPLETED`，或「`STOPPED` 且 `completion_reason == manual_stop` 且 `collection_stopped` 且每格皆為終局狀態」時為真。
- 優先序不變：停止時仍有未處理的衝突或未確認警報，先維持 Review，處理完才變成 MANUAL_STOP_COMPLETE。
- 整輪人工停止的原因要保留在快照與稽核中；即使每格都已有終局結果、沒有任何 STOPPED 格，也要顯示 MANUAL_STOP_COMPLETE，不能靠「有沒有 STOPPED 格」推測。
- 五種圖樣都恰好兩黑兩白，任一格翻轉都不會變成另一個合法圖樣；未定義圖樣（例如 0101）、缺格、低對比一律 unknown。
- 稽核紀錄保留 `completion_reason = manual_stop` 與原始 `STOPPED` 狀態，不改寫成 FAIL。產品處置由 LabVIEW 依「非 PASS 即不良」處理。
- **未見本次監控中就人工停止**：Log Solution 照常顯示 MANUAL_STOP_COMPLETE；橋接因為沒有本輪的同輪證據，不自動交付，改回報「需人工核對」（交接文件 9.2）。
- 舊 1.1 consumer 不認得 0011，**必須先更新橋接，才能部署 Log Solution 1.2**（與 C6 相同）。

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
- 幾何表更新左定位方塊座標（C6）；圖樣、排數標記不變。註明「1.2 以兩個定位方塊的距離與 1.1 區分，沒有另外的版本標記」。
- 新增格子顏色表（C7）。
- 開頭「App and upper computer must update together」改為：**1.2 起，上位機（橋接程式）先更新為同時支援 1.1 與 1.2 畫面，再更新 App；不需要兩邊同一天配對換版。** 上位機在所有 App 換成 1.2 之後，才會移除 1.1 支援。

### C6 左定位方塊移到色帶最左側上方（解決 P7）

**已決定（2026-10-17，使用者 A13）**：拿掉左上角「KVM RESULT」字樣，再把左定位方塊移到空出來的位置，也就是結果色帶第 1 格的正上方。左右兩個定位方塊因此涵蓋整條色帶：ATE 視窗從左側蓋到任何一格結果，一定先蓋到左定位方塊。

實際座標由 Log Solution 開發者決定，但必須符合以下五條限制條件，並寫回契約文件的幾何表：

1. **涵蓋色帶左緣**：左定位方塊的左邊緣，要對齊或超過結果色帶第 1 格的左邊緣（x 不大於第 1 格的 x）。
2. **留白與不對稱圖樣**：四周保留至少 2 單位的白色留白；維持現有的「內嵌白方塊」不對稱圖樣（左方塊的白方塊在左上、右方塊的白方塊在右下），讓橋接程式能分辨方向。
3. **大小與高度**：左右兩個定位方塊大小都是 22×22，且在同一高度（相同 y）。
4. **不重疊**：不和排數標記、狀態標記、結果色帶重疊，彼此之間保留留白。
5. **移除字樣**：「KVM RESULT」字樣直接拿掉，不移到其他位置，避免文字的黑色筆畫被誤認為定位方塊。

不變的部分：右定位方塊、排數標記、狀態標記、結果色帶的位置與顏色。

對橋接程式的影響：這是幾何變更。兩個定位方塊的距離由 238 單位變成約 320 單位（實際值依新座標而定），1.1 consumer 讀不到新版畫面，所以橋接程式要更新。橋接過渡期會同時支援兩種距離（票 #32），部署時**先更新橋接、再更新 Log Solution**。

### C7 格子顏色寫進契約（解決 P8）

在契約文件新增下表，作為格子顏色的唯一來源；`STATUS_COLOURS` 必須與它一致。

| 狀態 | 顏色 | 是否為最終狀態 |
| --- | --- | --- |
| PASS | `#00ef00` | 是 |
| FAIL | `#ff0000` | 是 |
| NOTEST | `#f04bf1` | 是 |
| TIMEOUT | `#ff9900` | 是 |
| STOPPED | `#bfbfbf` | 是 |
| TESTING | `#ffff00` | 否 |
| WAITING | `#d9d9d9` | 否 |
| COMPLETING | `#82c7ff` | 否 |
| 通道數以外的格子 | `#000000` | — |

- **移除 `STALLED`**：目前除了顏色表以外沒有任何程式使用，而且顏色和 `TIMEOUT` 相同。若日後需要「停滯」狀態，必須先在契約中新增一個和現有顏色明顯不同的顏色，並標明不是最終狀態，橋接配合更新後才能使用。
- **容許誤差**：接收端取每格四個角落的取樣，以 RGB 歐氏距離 ≤ 20 對應到最近的顏色。任兩種狀態的顏色，歐氏距離必須大於 40，判定範圍才不會重疊。上表已符合，但 **WAITING 與 STOPPED 只差約 45**（兩者都是灰色），餘裕很小。目前影響不大，因為 Complete 時不會有 WAITING；若日後要調整顏色，建議讓 STOPPED 和 WAITING 拉開距離。
- **NOTEST** 以 `#f04bf1` 為準，橋接程式改成同一個值。
- 顏色是 1.1 既有的值（除了移除 `STALLED`），所以這一條不影響橋接判讀，可以和 C5 一起發布。

## 4. 相容性

| Log Solution | 橋接程式 | 結果 |
| --- | --- | --- |
| 1.1（現況） | 現況／新版 | 人工停止後顯示 Standby，橋接回 `round_lost`，LabVIEW 暫停並通知人員，不交付結果（安全，不會誤判） |
| 1.2 | 現況（只支援 1.1 幾何） | **讀不到畫面**（左定位方塊位置不同），`check` 會點 Dock 恢復後回 `log_solution_not_visible`；不會誤判，但無法交付 |
| 1.2 | 現況（不認得 0011） | 人工停止畫面（0011）判為 unknown，`check` 點 Dock 後回 `log_solution_not_visible`；不會誤判，但無法交付 |
| 1.2 | 支援新舊幾何與 0011 的版本 | 人工停止的輪次以 MANUAL_STOP_COMPLETE 交付並回報人工停止，`results` 含 `STOPPED`；未見本次監控中則回需人工核對；左側遮擋一律判為不可見並點 Dock 恢復 |

**部署順序：先更新橋接程式（支援新舊兩種幾何），再更新 Log Solution 1.2。** 反過來會讓 1.2 的畫面暫時讀不到。

## 5. 驗收建議（Log Solution 專案）

1. `state_for_round_snapshot` 單元測試：
   - 人工停止、全部終局、無衝突 → MANUAL_STOP_COMPLETE（0011）；一般完成仍為 Complete（0110）。
   - 人工停止但有未處理衝突或未確認警報 → Review；處理後才變成 MANUAL_STOP_COMPLETE。
   - 人工停止且每格都已有終局結果（沒有 STOPPED 格）→ 仍為 MANUAL_STOP_COMPLETE。
   - 啟動失敗或未開始 → Standby。
   - `COMPLETED` → Complete（行為不變）。
2. `stop()` 在 `monitor is None` 時，所有非終局格子變成 `STOPPED`，`result_available` 為真。
3. 視窗前景：開始監控、進入 Review、`TIMEOUT`／`STOPPED` 結果、輪次結束時都會呼叫不搶焦點的前景函式（以假 root 驗證呼叫）。
4. FCT profile `start` 期限設為 120 秒時：監控開始後 100 秒才出現第一筆檔案變動，該格不會被鎖成 `NOTEST`。
5. 左定位方塊位於色帶第 1 格正上方，符合 C6 五條限制條件；以 Tk 擷取確認「KVM RESULT」字樣已移除、左右定位方塊與排數標記、狀態標記、色帶互不重疊，並在縮放 1.0／1.5 下都成立。
6. 單元測試：`STATUS_COLOURS` 與契約顏色表逐項相同，且不含 `STALLED`；任兩種顏色在每個通道相差超過 40。
7. 以 `tools/smoke_state_marker_app.py` 產生「人工停止 → MANUAL_STOP_COMPLETE（0011）」的實際 Tk 擷取，交給橋接程式用既有 frame replay 工具驗證 `check` 回 `kind=taken`、標明人工停止、`results` 含 `STOPPED`。

## 6. 橋接程式與部署的配合事項（供參考，不需 Log Solution 處理）

- 橋接新增三站共用的 `monitor_start`：先確認 Dock 上有 Log Solution 圖示並點擊，確認畫面上看得到 Log Solution，再送 Cmd+Shift+M，等待標記變成 Monitoring。
- 橋接 `check` 長輪詢期間，如果一段時間都找不到 Log Solution，會點 Dock 圖示嘗試拉回前面；仍然找不到才回錯誤給 LabVIEW。
- FCT 改為「治具推入前就發訊號給 LabVIEW」，LabVIEW 先 `monitor_start` 再讓治具推入。這需要和設備端協調。
- 部署 SOP：ATE 測試程式不可放大、不可蓋住畫面右側；Log Solution 各機台 profile 的通道數必須等於橋接設備清單的測試通道數。
