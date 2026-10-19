# LabVIEW 設計者交接：收到什麼、要做什麼

日期：2026-10-19（Asia/Taipei）
對象：LabVIEW 測試流程控制者的設計與實作人員
依據：spec #40（橋接協定修訂）、`docs/PROTOCOL.md`（協定 1.0）、`docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md`
狀態：**已實作**（spec #40，橋接 #41～#47，2026-10-19）。欄位型別、JSON 範例、typedef 以 `docs/PROTOCOL.md` 為準；本文件只說明呼叫順序與每種回覆的處置。三點補充（準備中即建立本輪開始請求、判讀順序把相同簽章的重看排在請求檢查之前、人工停止完成與一般完成同屬 READY）是使用者 2026-10-19 的決定，規則見 PROTOCOL 第 5.4～5.6 節。

---

## 1. 基本規則

- 每台設備一條 TCP 連線，一次只送一筆請求，收到回覆後才送下一筆；每筆請求只會有一筆回覆。
- `req_id` 由 LabVIEW 產生且每筆不同；回覆會原樣帶回 `req_id`、`device`、`cmd`，三者都核對相同才採用。
- 先看 `status`：`ok` 才看 `data.kind`；`error`／`timeout` 依第 4、5 節處理。
- `timeout_sec` 是 **LabVIEW 願意等多久**，不是 Log Solution 的測試期限。LabVIEW 的 TCP Read timeout 要比 `timeout_sec` 長（建議多 30 秒）。
- **只有 `kind = "taken"` 可以觸發設備動作**（分料、良品／不良品處置、放行下一批）。其他任何回覆都不能。

## 2. 三站呼叫順序

| 站 | 順序 |
| --- | --- |
| DFU | `input`（只填 SN）→ `monitor_start` → `start_test`（點 OK）→ `check_start`（可省略）→ `check_finish` |
| BT | `monitor_start` → `start_test`（點 Start All）→ `check_start`（可省略）→ `check_finish` |
| FCT | 治具推入**前**收到訊號 → `monitor_start` →（治具推入後 FCT 自動開測）→ `check_start`（可省略）→ `check_finish` |

說明：

- `monitor_start` 成功只代表「Log Solution 已準備好收集本輪資料」，**不代表 ATE 已開始測試**。
- `check_start` 確認「ATE 真的開始測試了」，並告訴你哪些通道已開始。可以不呼叫，也可以重複呼叫看進度；不會影響結果。
- `check_finish` 取得整輪結果。
- 每一輪都必須重新 `monitor_start`；不能沿用上一輪。

## 3. `check_start`／`check_finish` 的 `kind`

| `kind` | 意思 | 出現在 | `results` | LabVIEW 該做什麼 |
| --- | --- | --- | --- | --- |
| `started` | 已確認有通道開始測試；`active` 列出通道與證據。`reason` 為 `activity_confirmed` | `check_start`、`inspect`（Monitor UI） | 空 | 記錄已開始的通道（可顯示在人機畫面），接著送 `check_finish`。`inspect` 只顯示 |
| `taken` | 本輪完整結果，**第一次**交付 | `check_start`、`check_finish` | 完整 | **依逐格結果做產品處置**：PASS 良品；FAIL／NOTEST／TIMEOUT／STOPPED 不良品交人工。`state = "manual_stop_complete"` 時標記為人工停止、交作業員重工 |
| `already_taken` | 同一輪結果**再次**交付（重看或通訊中斷後重取），結果與第一次相同 | `check_start`、`check_finish` | 完整（與第一次相同） | 只顯示、核對；**不再做設備動作** |
| `waiting` | 還沒有可交付結果：準備中、監控中但尚未開始、測試中 | `check_start`、`check_finish`、`inspect` | 空 | 繼續等或再查；`state` 可顯示目前狀態 |
| `paused` | Log 異常待確認（衝突、警報、未偵測到測試開始等） | `check_start`、`check_finish`、`inspect` | 空 | 報警、暫停後續設備動作，等人員在 Log Solution 處理完再查 |
| `unknown` | 畫面看不清楚，判讀不可靠 | `check_start`、`check_finish`、`inspect` | 空 | 視為設備／通訊異常，**不得判產品 FAIL** |

`active` 的 `evidence`：

- `testing`：看到該通道 TESTING／COMPLETING。
- `result`：沒看到測試中，但已看到該通道的有效測試結果（PASS／FAIL 等）。
- NOTEST、TIMEOUT、STOPPED、WAITING 都**不算**已開始。

`state` 的值：`standby`（待命）、`preparing`（準備中，不能開測）、`monitoring`（監控就緒）、`review`（待確認）、`complete`（一般完成）、`manual_stop_complete`（人工停止完成）、`""`（看不清楚）。

`check_start`／`check_finish` 先看待確認（`paused`），再看是否與上一次交付簽章相同（`already_taken`，附相同結果），再看畫面是否清楚，最後才檢查本輪開始請求。沒有請求回 `monitor_not_ready`。交付後簽章變了、又沒看到準備中或監控中、也沒有新的 `monitor_start`，同樣回 `monitor_not_ready`。`inspect` 不套用這條請求規則，也不回 `taken`。

## 4. 錯誤碼與處置（`status = "error"`）

| `error` | 何時 | LabVIEW 處置 |
| --- | --- | --- |
| `manual_verification_required` | 本輪送過 `monitor_start`，但橋接一直沒看到本次監控就緒，畫面卻出現可交付結果（例如還沒就緒就被人工停止、或準備逾時後人員確認警報） | **不交付、不做設備動作、不自動重測**；暫停並請人員核對。核對後由人員決定重工，下一輪重新 `monitor_start` |
| `monitor_not_ready` | `check_start`／`check_finish` 沒有本輪開始請求（沒送過 `monitor_start`，或交付後簽章已變且沒有新一輪信號）；或 `start_test` 時 Log 不是監控就緒（例如準備中） | 流程順序錯誤：先完成 `monitor_start` |
| `monitor_not_started` | `monitor_start` 在 `timeout_sec` 內沒看到監控就緒 | 暫停並通知人員；可依現場判斷再送一次 `monitor_start`（Log 仍在準備中時不會重複觸發） |
| `log_solution_review` | `monitor_start` 時 Log 異常待確認（例如啟動失敗） | 報警、暫停；人員在 Log Solution 排除並復位後才能再 `monitor_start` |
| `log_solution_busy` | `monitor_start` 時 Log 已在監控中；或 `input` 時為監控中／待確認／準備中，或一般完成／人工停止完成但本輪尚未交付、也沒回過 `manual_verification_required` | 上一輪尚未結束，先完成本輪或等人工核對。待命，以及完成類狀態在已交付或已人工核對後，可以輸入下一輪 SN |
| `log_solution_not_found` | Log Solution 沒開，或 Dock 上找不到它 | 嚴重異常，停線檢查 |
| `log_solution_not_visible` | Log Solution 被遮住，自動拉回 3 次仍看不到 | 暫停並請人員把 Log Solution 視窗移出來 |
| `round_lost` | 本輪已監控就緒後，Log Solution 回到待命（例如當掉重開） | 本輪結果已無法取得，暫停並交人工 |
| `capacity_mismatch` | 畫面格數和設定的測試通道數不同 | 設定錯誤，暫停檢查 Log Solution 與設備清單 |
| `frame_frozen` | 畫面停止更新 | 設備異常，暫停 |
| `device_busy` | 同一台設備已有指令在執行 | 不要並行送指令，稍後再送 |
| 其他（`invalid_*`、`unknown_*`、`forbidden_field` 等） | 請求格式錯誤 | 修正 LabVIEW 程式 |

以上錯誤都是設備、通訊或流程異常，**都不是產品結果**，不得判產品 FAIL。

## 5. 逾時（`status = "timeout"`）

- `check_start`／`check_finish` 等到 `timeout_sec` 仍沒有可回報的狀態時回 `timeout`，`data` 帶最後一次看到的狀態。
- 這只表示「LabVIEW 這次等完了」，不代表測試失敗。依 `data.state` 決定再查、報警或交人工。

## 6. 什麼時候可以做設備動作

| 可以 | 不可以 |
| --- | --- |
| 收到 `taken` | `started`、`already_taken`、`waiting`、`paused`、`unknown` |
| | 任何 `status = "error"` 或 `"timeout"` |

`already_taken` 可以拿來在人機畫面重新顯示結果，或在通訊中斷後確認上次交付的內容，但設備動作只能在第一次 `taken` 時做一次。

## 7. 尚未包含（協定 1.1 再處理）

- 輪次 UUID、結果接收確認（ACK）、橋接程式重啟後重傳同輪結果。
- 人工核對後取消本輪、換新輪次 ID 的對外流程。
