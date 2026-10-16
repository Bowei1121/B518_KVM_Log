# LabVIEW ↔ JetKVM Log 橋接程式 TCP JSON 介面規格

版本：`protocol_version = "1.0"`
日期：2026-10-16（Asia/Taipei）
狀態：已在 Core Service 實作（spec #1 的 #2～#11；spec #2 的 #22～#26：三站統一 Log Solution 判讀、`monitor_start`、`start_test`）。LabVIEW 端的 type definition 與 VI 改版尚待 #13（人工）。實作對照與待辦見第 10 節。
相關：`CONTEXT.md`、ADR-0002／0003／0006、`docs/LABVIEW_LOG_SOLUTION_INTEGRATION.md`、`docs/specs/0002-log-solution-judgement-all-stations.md`。

輪次 UUID、結果保存、接收確認（ACK）不在 1.0 範圍，定案後以 1.1 加入。

---

## 1. 傳輸

| 項目 | 規定 |
| --- | --- |
| 傳輸層 | TCP，Core Service 預設 port `5000` |
| 編碼 | UTF-8，不加 BOM |
| 分隔 | 一行一個 JSON 物件，以 `\n`（LF, 0x0A）結尾。Core 容忍 `\r\n`，LabVIEW 送出時請用 `\n` |
| 連線 | 每台設備一條獨立 TCP 連線（ADR-0006）。同一條連線上**一次只送一筆請求**，收到回覆後才送下一筆 |
| 回覆順序 | 一筆請求只回一行；以 `req_id` 配對 |
| 最大行長 | `snapshot` 回覆含 base64 影像，可能超過 1 MB；LabVIEW 讀取時請用 CRLF/LF 模式並給足夠的最大長度 |

JSON 內不使用 `null`。字串欄位沒有值時為 `""`，數字為 `0`，陣列為 `[]`，布林為 `false`。

## 2. 請求共同欄位

所有請求都必須帶以下四個欄位，型別必須相符：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `protocol_version` | string | 是 | 固定 `"1.0"` |
| `req_id` | string | 是 | LabVIEW 產生，1–64 字元，每筆請求不重複（建議用 GUID）。Core 原樣回傳。**不是輪次 UUID** |
| `device` | string | 是 | 設備 ID，必須與 Core 設備清單**完全相同**（區分大小寫），例如 `DFU-01`。`ping`、`status` 填 `""` |
| `cmd` | string | 是 | 指令名稱，見第 4 節 |

嚴格規則：

- 不認得的欄位一律拒絕（`unknown_field`），避免拼錯欄位名卻被默默忽略。
- **不得**帶 `kvm_ip`、`station`；IP 與站別由 Core 依設備清單決定（`forbidden_field`）。
- 指令專屬欄位見各指令；沒列出的欄位即為不認得的欄位。

## 3. 回覆共同欄位（envelope）

每一筆回覆都**固定**包含以下欄位，不論成功或失敗：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `protocol_version` | string | `"1.0"` |
| `req_id` | string | 與請求相同；請求無法解析時為 `""` |
| `device` | string | 與請求相同；無法解析時為 `""` |
| `cmd` | string | 與請求相同；無法解析時為 `""` |
| `status` | string | `ok`／`error`／`timeout` |
| `error` | string | `status=ok` 時為 `""`；其他見第 6 節錯誤碼 |
| `message` | string | 給人看的說明，LabVIEW 不應依此判斷流程 |
| `elapsed_sec` | number | Core 處理該請求所花秒數 |
| `data` | object | 該指令固定欄位的物件；**錯誤或逾時時仍會出現**。欄位未取得時為預設值；`check` 的逾時與部分錯誤會帶最後一次觀察值（見 5.4） |

LabVIEW 建議做法：以送出的 `cmd` 決定用哪一個完整 typedef（envelope + 該指令的 `data`）做 Unflatten From JSON，再核對 `req_id`、`device`、`cmd` 三者與請求相同後才採用。

## 4. 指令一覽

| `cmd` | 用途 | 上鎖 | DFU | FCT | BT |
| --- | --- | --- | --- | --- | --- |
| `ping` | 確認 Core 存活 | 否 | — | — | — |
| `status` | 取得 Core 與各設備連線狀態（供 LabVIEW 人機畫面顯示） | 否 | — | — | — |
| `snapshot` | 取一張當前畫面 | 否 | ✔ | ✔ | ✔ |
| `monitor_start` | 從 Dock 喚起 Log Solution，送 Cmd+Shift+M，確認進入監控中 | 是 | ✔ | ✔ | ✔ |
| `start_test` | 開始測試：DFU 點 OK、BT 點 Start All | 是 | ✔ | ✘ | ✔ |
| `check` | 讀取 Log Solution 本輪狀態／結果，可長輪詢 | 是 | ✔ | ✔ | ✔ |
| `inspect` | Monitor UI 唯讀檢視（僅供 Monitor UI，LabVIEW 不得使用） | 否 | ✔ | ✔ | ✔ |
| `input` | 勾選通道並輸入本輪 SN（不點 OK、不開始監控） | 是 | ✔ | ✘ | ✘ |

- 「上鎖」指令在同一台設備已有上鎖指令執行中時，立刻回 `device_busy`（ADR-0002 fail-fast）。不同設備互不影響。
- 站別不支援的指令回 `unsupported_command`（例如 FCT 的 `start_test`、DFU 以外的 `input`）。
- `button` 已移除，改由 `start_test` 開始測試。送出 `button` 回 `unknown_command`。
- 三站的 `check`／`inspect` 一律以 Log Solution 顯示契約判讀，站別只影響操作指令。

### 4.1 三站呼叫順序

先監控、後開測。前一步回 `status = "ok"` 才進行下一步；`error` 依第 6 節處理。

| 站別 | 呼叫順序 |
| --- | --- |
| DFU | `input` → `monitor_start` → `start_test` → `check` |
| BT | `monitor_start` → `start_test` → `check` |
| FCT | 治具推入前設備發訊號 → `monitor_start` → `check` |

- FCT 沒有 `start_test`。治具推入後由 ATE 自行開測，LabVIEW 必須在推入前完成 `monitor_start`。
- `monitor_start` 只在 Log Solution 為 Standby 或 Complete 時送出快捷鍵；Monitoring、Review 回 `log_solution_busy`。
- `input` 只在 Standby，或 Complete 且上一輪已由 `check` 取用時允許（見 5.5）。
- `check` 必須在本輪見過 Monitoring 之後，才會接受完成畫面。因此 `monitor_start` 成功是取得本輪結果的前提。

## 5. 各指令

### 5.1 `ping`

請求：
```json
{"protocol_version":"1.0","req_id":"5c1e...","device":"","cmd":"ping"}
```
`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `pong` | boolean | 固定 `true` |
| `core_version` | string | Core Service 版本 |

### 5.2 `status`

請求只有共同欄位，`device` 填 `""`。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `core_uptime_sec` | number | Core 已運行秒數 |
| `devices` | array of object | 依設備清單順序 |
| `devices[].device` | string | 設備 ID |
| `devices[].station` | string | `DFU`／`FCT`／`BT` |
| `devices[].connected` | boolean | KVM 連線池中是否已連線 |
| `devices[].busy` | boolean | 是否有上鎖指令執行中 |
| `devices[].frozen` | boolean | 畫面凍結檢查是否判定凍結 |
| `devices[].channels` | number | 設定的測試通道數 |

KVM IP 不回給 LabVIEW，LabVIEW 也不需要知道。

### 5.3 `snapshot`

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `format` | string | 是 | `jpeg` 或 `png` |
| `quality` | number | 是 | 1–100，僅 `jpeg` 有效；`png` 請填 `0` |

`format` 不是 `jpeg`／`png`、`quality` 缺少或非整數、`jpeg` 的 `quality` 不在 1–100、`png` 的 `quality` 不為 `0`，皆回 `invalid_argument`，且不連線 KVM。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `width` | number | 像素寬 |
| `height` | number | 像素高 |
| `format` | string | 實際格式 |
| `image_base64` | string | 影像 base64 |

### 5.4 `check`

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `timeout_sec` | number | 是 | `0` = 只讀一次立即回覆；`> 0` = 長輪詢，最多 `3600` |

`timeout_sec` 白話：LabVIEW 願意等多久。

- `0`：「現在畫面是什麼狀態？」看一眼馬上回，通常不到 1 秒。
- `600`：「有結果再告訴我，最多等 10 分鐘。」Core 會一直盯著畫面，結果出來、待確認、出錯就立刻回；10 分鐘到了還沒結果，回 `status = "timeout"`。
- LabVIEW 的 TCP Read timeout 必須**大於** `timeout_sec`（建議多 30 秒），否則 LabVIEW 會先斷線。

長輪詢在下列任一情況**立即回覆**，否則等到逾時：

1. `data.kind = "taken"`：本輪結果已可靠取得，且格數等於該設備測試通道數。
2. `data.kind = "paused"`：Log Solution 待確認，LabVIEW 應暫停後續設備動作。
3. `data.kind = "already_taken"`：本輪結果先前已取用過。
4. 任何錯誤（例如 `frame_frozen`）。

逾時回覆 `status = "timeout"`、`error = "timeout"`，`data` 帶最後一次觀察到的狀態。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `kind` | string | `taken`／`waiting`／`paused`／`already_taken`／`unknown` |
| `state` | string | Log Solution 標記：`standby`／`monitoring`／`review`／`complete`；無法辨識為 `""` |
| `reason` | string | 細部原因，見下表；僅供記錄 |
| `capacity` | number | 畫面結果格數 1–20；無法辨識為 `0` |
| `results` | array of object | 僅 `kind = "taken"` 時有內容，其餘為 `[]` |
| `results[].slot` | number | 1–20；第一排左至右 1–10，第二排 11–20 |
| `results[].status` | string | `PASS`／`FAIL`／`NOTEST`／`TIMEOUT`／`STOPPED`（人員手動停止監控，原樣回傳） |

`kind` 與 LabVIEW 動作：

| `kind` | 意義 | LabVIEW 動作 |
| --- | --- | --- |
| `taken` | 本輪完整結果已可靠取得 | 依本輪參與清單對應 SN；PASS 良品，FAIL／NOTEST／TIMEOUT 不良品；STOPPED 判不良並交作業員重工 |
| `waiting` | 監控中／尚未完成 | 繼續 `check` |
| `paused` | 待確認 | 暫停設備動作，等人員在 Log Solution 處理後再 `check` |
| `already_taken` | 本輪已取用過 | 不重做設備動作；重傳機制屬 1.1 |
| `unknown` | 畫面不可辨識 | 視為設備／通訊異常，不得判產品 FAIL |

`reason` 目前值：`standby`、`monitoring`、`monitoring_not_observed`、`confirming_complete_frame`、`incomplete_or_nonterminal_results`、`round_capacity_changed`、`confirmation_required`、`round_already_taken`、`reliable_complete`、`no_frame`、`missing_source_frame_time`、`out_of_order_source_frame`，以及畫面定位失敗類原因。LabVIEW 不應依 `reason` 分支，只依 `kind`。

**沒有總結果欄位**（舊 `result=PASS` 移除）。產品處置只能依 `results` 逐格判斷，結果色帶不含 SN。

#### 可見性與遺失

- 連續 60 秒判讀為「找不到 Log Solution」（`kind = unknown`，`reason` 為定位失敗類：`locators_missing_or_ambiguous`、`locator_orientation_or_contrast_invalid`）：Core 點一次 Log Solution 的 Dock 圖示，等 5 秒後重新判讀。
- 同一請求最多點 3 次，仍看不到回 `error = "log_solution_not_visible"`。
- 本輪在串流上已見過 Monitoring，之後判讀到 Standby：立即回 `error = "round_lost"`。本輪結果已無法取得，LabVIEW 應暫停並通知人員。
- 以上兩者與其他錯誤一樣是 `status = "error"`，`data.kind` 為 `unknown`。`data` 其餘欄位可能保留最後一次觀察值，LabVIEW 只依 `error` 判斷。
- `inspect` 為唯讀，不點 Dock。

範例（取得結果）：
```json
{"protocol_version":"1.0","req_id":"9a7f...","device":"DFU-01","cmd":"check","status":"ok","error":"","message":"","elapsed_sec":312.4,"data":{"kind":"taken","state":"complete","reason":"reliable_complete","capacity":4,"results":[{"slot":1,"status":"PASS"},{"slot":2,"status":"FAIL"},{"slot":3,"status":"NOTEST"},{"slot":4,"status":"PASS"}]}}
```

範例（逾時）：
```json
{"protocol_version":"1.0","req_id":"9a80...","device":"DFU-01","cmd":"check","status":"timeout","error":"timeout","message":"no reliable result within 600 s","elapsed_sec":600.0,"data":{"kind":"waiting","state":"monitoring","reason":"monitoring","capacity":4,"results":[]}}
```

### 5.5 `input`（僅 DFU）

流程：前景化 DFU 視窗（點 DFU Dock 圖示）→ 勾選請求的通道、取消其餘 → 逐筆輸入 SN。**不點 OK、不送 Cmd+Shift+M**。開始監控與開測分別由 `monitor_start`、`start_test` 負責。

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `items` | array of object | 是 | 1 筆以上 |
| `items[].slot` | number | 是 | 測試通道編號 1～N（N = 該設備設定的測試通道數），不可重複；與 Log Solution 通道／Slot 編號一致 |
| `items[].sn` | string | 是 | SN，1–64 字元；可含逗號、冒號 |

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `entered` | array of number | 實際完成輸入的位置 |

Core 在 DFU 輸入視窗找出所有 checkbox，數量必須等於 N，依由上而下、由左至右排序為通道 1～N；數量不符回 `hid_failed`，不輸入任何 SN。

只有 Log Solution 在 `standby`，或 `complete` 且本輪結果已取用時才允許輸入；`monitoring`／`review` 時回 `log_solution_busy`，不輸入任何 SN。

### 5.6 `monitor_start`（DFU、FCT、BT）

無專屬請求欄位。上鎖。

流程（依序，任一步失敗即停止，未執行的步驟不送 HID）：

1. 讀取最新畫面。Log Solution 為 Monitoring 或 Review → `log_solution_busy`，不送 HID。
2. 定位共用 Dock 圖示模板 `COMMON/COMMON_log_solution_dock_icon.png` 並點擊一次。模板缺失或 HID 錯誤 → `hid_failed`；圖示不在畫面上 → `log_solution_not_found`。
3. 點擊後 3 秒內須讀到可靠的 Log Solution 畫面，否則 `log_solution_not_found`。讀到 Monitoring 或 Review → `log_solution_busy`，仍不送快捷鍵。
4. 送出 Cmd+Shift+M。10 秒內該設備的判讀見 Monitoring → 成功；否則 `monitor_not_started`。
5. 成功時該設備的輪次判讀已視為看過 Monitoring，之後的 `check` 會取得本輪結果。

Dock 點擊在 Log Solution 已關閉時會讓 macOS 重新開啟它；開啟後為 Standby，流程可繼續。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `state` | string | 成功時為 `monitoring`；失敗為 `""` |
| `capacity` | number | 成功時畫面上的結果格數；失敗為 `0` |

### 5.7 `start_test`（DFU、BT）

無專屬請求欄位。上鎖。FCT 回 `unsupported_command`（FCT 於治具推入後自行開測）。

流程：

1. 讀取最新畫面判讀 Log Solution 標記。非 Monitoring、無法讀取或沒有影格 → `monitor_not_ready`，不送 HID。
2. DFU：點 DFU Dock 圖示前景化 DFU 視窗，找到 DFU 視窗與 OK 按鈕後點一次 OK。
   BT：點 BT Dock 圖示前景化 BT 視窗，找到 Start All 後點一次。
3. 任一模板找不到或 HID 錯誤 → `hid_failed`。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `action` | string | 成功時 DFU 為 `ok`、BT 為 `start_all`；失敗為 `""` |

### 5.8 `inspect`（僅供 Monitor UI）

Monitor UI 的手動檢查使用。欄位與 `check` 相同（`timeout_sec` 必填，0～3600），回覆格式相同（`data` 為同一結構）。

- 不上鎖：同一設備有 LabVIEW 上鎖指令執行中時仍可執行，不回 `device_busy`。
- 唯讀：不改變本輪狀態，不消耗 LabVIEW 的結果，LabVIEW 之後的 `check` 仍可取得 `taken`。
- 因為不經過本輪狀態，`inspect` 不會回 `already_taken`。完整畫面須連續兩次讀到相同結果才回 `taken`（`reason=reliable_complete`），僅代表「此刻畫面上的結果可靠」，不代表已交付給 LabVIEW。
- LabVIEW 不得使用本指令；Core 不保證它與 `check` 的長輪詢結果一致。

## 6. 錯誤碼

| `error` | `status` | 何時發生 | LabVIEW 建議 |
| --- | --- | --- | --- |
| `invalid_json` | error | 該行不是合法 JSON | 修正程式 |
| `invalid_request` | error | 不是 JSON 物件，或欄位型別錯誤 | 修正程式 |
| `unsupported_protocol_version` | error | `protocol_version` 不是 `1.0` | 修正程式 |
| `missing_req_id` | error | 未帶 `req_id` 或為空 | 修正程式 |
| `missing_command` | error | 未帶 `cmd` | 修正程式 |
| `unknown_command` | error | `cmd` 不存在 | 修正程式 |
| `missing_device` | error | 需要設備的指令 `device` 為空 | 修正程式 |
| `unknown_device` | error | `device` 不在設備清單 | 檢查設備 ID |
| `unknown_field` | error | 帶了未定義欄位 | 修正程式 |
| `forbidden_field` | error | 帶了 `kvm_ip` 或 `station` | 修正程式 |
| `invalid_argument` | error | 指令欄位值不合法（input 的 items 格式或 slot 超出範圍、snapshot 的 format／quality 不合法、timeout 超出範圍等） | 修正程式 |
| `unsupported_command` | error | 站別不支援此指令（例如 FCT 的 `start_test`、DFU 以外的 `input`） | 修正程式 |
| `log_solution_busy` | error | `input` 或 `monitor_start` 時 Log Solution 為 `monitoring` 或 `review`；`input` 另含 `complete` 但本輪未取用 | 先完成本輪 `check`／人工確認 |
| `capacity_mismatch` | error | `check` 畫面結果格數與設定的測試通道數不同 | 檢查 Log Solution 設定，暫停 |
| `device_busy` | error | 同設備已有上鎖指令執行中 | 稍後重試，勿並行送 |
| `kvm_connect_failed` | error | 無法連線該設備的 JetKVM | 設備／網路異常，暫停 |
| `no_frame` | error | 尚未收到畫面 | 稍後重試 |
| `frame_frozen` | error | 畫面時間戳不再前進（ADR-0005） | 設備異常，暫停，不得判產品 FAIL |
| `hid_failed` | error | 鍵盤／滑鼠操作失敗、視窗或按鈕定位失敗、模板缺失；`input`／`monitor_start` 沒有影格 | 設備異常，暫停 |
| `log_solution_not_found` | error | `monitor_start`：Dock 上找不到 Log Solution 圖示，或點擊後 3 秒內找不到 Log Solution 視窗；未送快捷鍵 | 環境異常，暫停，請人員開啟或前景化 Log Solution |
| `monitor_not_started` | error | `monitor_start`：送出 Cmd+Shift+M 後 10 秒內未見 Monitoring | 設備／環境異常，暫停，不開測 |
| `monitor_not_ready` | error | `start_test`：Log Solution 非 Monitoring、無法讀取或沒有影格；未送 HID | 先完成 `monitor_start` |
| `log_solution_not_visible` | error | `check`：連續 60 秒看不到 Log Solution，點 Dock 3 次仍看不到 | 環境異常，暫停，不得判產品 FAIL |
| `round_lost` | error | `check`：本輪已見 Monitoring 後 Log Solution 回到 Standby（1.1 下人工停止會落入此處） | 本輪結果已無法取得，暫停，不得判產品 FAIL（見 5.4） |
| `timeout` | timeout | `check` 長輪詢逾時 | 依流程決定是否再 `check` |
| `server_shutting_down` | error | Core 正在關閉 | 重連後重試 |
| `internal_error` | error | Core 內部例外 | 記錄並通報 |

`log_solution_not_found`、`monitor_not_started`、`monitor_not_ready`、`log_solution_not_visible`、`round_lost` 是設備、通訊或環境異常，不是產品結果。這些回覆一律 `status = "error"`，不會出現 `kind = "taken"`；LabVIEW 依 `error` 暫停，不得據此判產品 FAIL。`monitor_not_ready` 是流程順序錯誤，同樣未送出任何 HID。

`button` 已移除，送出回 `unknown_command`。

## 7. 連線中斷

- LabVIEW 在長輪詢中斷線時，Core 取消該 `check` 並釋放設備鎖，不回覆。
- 重連後重新送 `check`。結果重傳、ACK 與重啟恢復屬 1.1。

## 8. LabVIEW typedef 清單（建議）

| typedef | 內容 |
| --- | --- |
| `Envelope` | 第 3 節除 `data` 外欄位 |
| `PingResp` | Envelope + `data{pong, core_version}` |
| `StatusResp` | Envelope + `data{core_uptime_sec, devices[]}` |
| `SnapshotResp` | Envelope + `data{width, height, format, image_base64}` |
| `CheckResp` | Envelope + `data{kind, state, reason, capacity, results[]{slot, status}}` |
| `InputResp` | Envelope + `data{entered[]}` |
| `MonitorStartResp` | Envelope + `data{state, capacity}` |
| `StartTestResp` | Envelope + `data{action}` |

## 9. 完整範例

每組為「LabVIEW 送出的一行」→「Core 回覆的一行」。實際傳輸時每筆都是單行，這裡為了閱讀而換行排版。

### 9.1 `ping`

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0001","device":"","cmd":"ping"}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0001","device":"","cmd":"ping",
 "status":"ok","error":"","message":"","elapsed_sec":0.0,
 "data":{"pong":true,"core_version":"2.0.0"}}
```

### 9.2 `status`

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0002","device":"","cmd":"status"}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0002","device":"","cmd":"status",
 "status":"ok","error":"","message":"","elapsed_sec":0.0,
 "data":{"core_uptime_sec":3600,"devices":[
   {"device":"DFU-01","station":"DFU","connected":true,"busy":false,"frozen":false,"channels":4},
   {"device":"FCT-01","station":"FCT","connected":false,"busy":false,"frozen":false,"channels":4},
   {"device":"BT-01","station":"BT","connected":true,"busy":true,"frozen":false,"channels":4}]}}
```

### 9.3 `snapshot`

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0003","device":"DFU-01","cmd":"snapshot","format":"jpeg","quality":80}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0003","device":"DFU-01","cmd":"snapshot",
 "status":"ok","error":"","message":"","elapsed_sec":0.05,
 "data":{"width":1920,"height":1080,"format":"jpeg","image_base64":"/9j/4AAQSkZJRg..."}}
```

### 9.4 `input`（DFU）

SN 可以含逗號、冒號，JSON 字串會自動處理，不需要跳脫。

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0004","device":"DFU-01","cmd":"input",
 "items":[{"slot":1,"sn":"F5K2-001"},{"slot":3,"sn":"F5K2:003,B"}]}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0004","device":"DFU-01","cmd":"input",
 "status":"ok","error":"","message":"","elapsed_sec":8.7,
 "data":{"entered":[1,3]}}
```

Log Solution 仍在監控中時（上一輪尚未結束）：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0005","device":"DFU-01","cmd":"input",
 "status":"error","error":"log_solution_busy","message":"Log Solution state is monitoring","elapsed_sec":0.3,
 "data":{"entered":[]}}
```

### 9.5 `monitor_start`（DFU）

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0006","device":"DFU-01","cmd":"monitor_start"}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0006","device":"DFU-01","cmd":"monitor_start",
 "status":"ok","error":"","message":"","elapsed_sec":4.2,
 "data":{"state":"monitoring","capacity":4}}
```

### 9.6 `start_test`（BT）

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0012","device":"BT-01","cmd":"start_test"}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0012","device":"BT-01","cmd":"start_test",
 "status":"ok","error":"","message":"","elapsed_sec":1.2,
 "data":{"action":"start_all"}}
```

### 9.7 `check`

只看一眼（`timeout_sec = 0`），目前監控中：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0007","device":"DFU-01","cmd":"check","timeout_sec":0}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0007","device":"DFU-01","cmd":"check",
 "status":"ok","error":"","message":"","elapsed_sec":0.2,
 "data":{"kind":"waiting","state":"monitoring","reason":"monitoring","capacity":4,"results":[]}}
```

長輪詢等到待確認，立即回：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0008","device":"DFU-01","cmd":"check","timeout_sec":600}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0008","device":"DFU-01","cmd":"check",
 "status":"ok","error":"","message":"","elapsed_sec":145.3,
 "data":{"kind":"paused","state":"review","reason":"confirmation_required","capacity":4,"results":[]}}
```

取得結果與逾時範例見 5.4。

### 9.8 常見錯誤

同一台設備已有動作在執行：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0009","device":"DFU-01","cmd":"input",
 "status":"error","error":"device_busy","message":"DFU-01 is busy with req 6f1d0c2e-0008","elapsed_sec":0.0,
 "data":{"entered":[]}}
```

設備 ID 大小寫錯誤：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0010","device":"dfu-01","cmd":"check",
 "status":"error","error":"unknown_device","message":"device 'dfu-01' is not configured","elapsed_sec":0.0,
 "data":{"kind":"","state":"","reason":"","capacity":0,"results":[]}}
```

帶了 `kvm_ip`：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0011","device":"DFU-01","cmd":"check",
 "status":"error","error":"forbidden_field","message":"field 'kvm_ip' is not allowed","elapsed_sec":0.0,
 "data":{"kind":"","state":"","reason":"","capacity":0,"results":[]}}
```

## 10. 實作對照與待辦

第 10 節原為 1.0 立案時的落差表。下表為目前 Core Service 的狀態。

| 項目 | 1.0 規定 | 目前實作 |
| --- | --- | --- |
| 設備身份 | 只帶設備 ID；KVM IP 與站別由設備清單決定 | 已實作。`kvm_ip`、`station` 回 `forbidden_field` |
| `req_id` | 必填，不自動補 | 已實作。缺少回 `missing_req_id` |
| `protocol_version`、`unknown_field` | 必檢 | 已實作 |
| 回覆欄位 | 固定 envelope＋固定 `data`，不出現 `null` | 已實作 |
| `input`、`monitor_start`、`start_test` | 正式指令；`button` 移除 | 已實作（DFU `input` 只勾選與填 SN；`monitor_start` 三站；`start_test` 取代 `button`） |
| 三站判讀 | FCT／BT 的 `check` 與 DFU 同一顯示契約判讀 | 已實作；模板判讀結果與 `testing`／`pass`／`fail` 模板已淘汰 |
| 可見性與遺失 | 看不到 Log Solution 點 Dock 恢復；Standby 回 `round_lost` | 已實作（60 秒、5 秒、最多 3 次） |
| 共用 Dock 模板 | `COMMON/COMMON_log_solution_dock_icon.png` | 已實作；創建 Pattern 不再建立結果模板 |
| `check` 長輪詢 | paused 立即回；無總結果欄位 | 已實作；`timeout_sec` 必填 |
| `check` 格數核對 | 格數 ≠ 通道數回 `capacity_mismatch` | 已實作 |
| 長輪詢間隔與判讀 | 0.5 秒；判讀不在事件迴圈，單張上限 | 已實作；單張上限預設 200 ms，超時記 `unknown` |
| DFU 輸入定位 | 只用 checkbox 模板，自動排序 | 已實作；錨點模板與 `device_profiles.json` 已淘汰 |
| `stations`／`status` | 新 `status`，內部狀態改名 `core_status` | 已實作；`stations` 併入 `core_status` |
| Monitor UI 手動檢查 | 唯讀、不上鎖 | 以 `inspect` 實作（見 5.8），僅供 Monitor UI |

### 仍待辦

- **STOPPED 的 header 狀態**：人員按停止監控後，Log Solution header 會變成哪一種，待現場確認。若維持 monitoring，STOPPED 結果需另外處理才能觸發 taken。
- **LabVIEW 端**：typedef、`TCPIP_test.vi` 改版為 JSON 1.0，屬 spec #13，需人工在 LabVIEW 完成。
- **FCT 推入前訊號的現場時序**：訊號到首次寫檔的實際時間，決定 Log Solution FCT profile 的 start 期限（建議先設 120 秒）。
- **Log Solution 1.2**：人工停止顯示 Complete、視窗前景時機等修改，依 `docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md` 由 Log Solution 開發者開票。本橋接不依賴 1.2；1.1 下人工停止的輪次回 `round_lost`。
- **JetKVM 靜態 IP**：正式版是否提供靜態 IP 設定，待現場確認（見 `docs/DEPLOYMENT.md`）。
