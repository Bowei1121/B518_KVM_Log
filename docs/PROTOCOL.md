# LabVIEW ↔ JetKVM Log 橋接程式 TCP JSON 介面規格

版本：`protocol_version = "1.0"`（草案）
日期：2026-10-15（Asia/Taipei）
狀態：設計草案，供 LabVIEW 設計 type definition。**尚未實作**；與現行程式的落差見第 10 節。
相關：`CONTEXT.md`、ADR-0002／0003／0006、`docs/LABVIEW_LOG_SOLUTION_INTEGRATION.md`。

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
| `device` | string | 是 | 設備 ID，必須與 Core 設備清單**完全相同**（區分大小寫），例如 `DFU-01`。`ping`、`devices` 填 `""` |
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
| `data` | object | 該指令固定欄位的物件；**錯誤或逾時時仍會出現，所有欄位為預設值** |

LabVIEW 建議做法：以送出的 `cmd` 決定用哪一個完整 typedef（envelope + 該指令的 `data`）做 Unflatten From JSON，再核對 `req_id`、`device`、`cmd` 三者與請求相同後才採用。

## 4. 指令一覽

| `cmd` | 用途 | 上鎖 | DFU | FCT | BT |
| --- | --- | --- | --- | --- | --- |
| `ping` | 確認 Core 存活 | 否 | — | — | — |
| `devices` | 取得設備清單與即時狀態 | 否 | — | — | — |
| `snapshot` | 取一張當前畫面 | 否 | ✔ | ✔ | ✔ |
| `check` | 讀取 Log Solution 本輪狀態／結果，可長輪詢 | 是 | ✔ | ✔ | ✔ |
| `input` | 輸入本輪 SN | 是 | ✔ | ✘ | ✘ |
| `button` | 按 Start All | 是 | ✘ | ✘ | ✔ |

- 「上鎖」指令在同一台設備已有上鎖指令執行中時，立刻回 `device_busy`（ADR-0002 fail-fast）。不同設備互不影響。
- 站別不支援的指令回 `unsupported_command`。
- `check` 對 FCT／BT 的 Log Solution 判讀為目標行為，尚未實作（第 10 節）。

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

### 5.2 `devices`

請求只有共同欄位，`device` 填 `""`。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `devices` | array of object | 依設備清單順序 |
| `devices[].device` | string | 設備 ID |
| `devices[].station` | string | `DFU`／`FCT`／`BT` |
| `devices[].connected` | boolean | KVM 連線池中是否已連線 |
| `devices[].busy` | boolean | 是否有上鎖指令執行中 |
| `devices[].frozen` | boolean | 畫面凍結檢查是否判定凍結 |

KVM IP 不回給 LabVIEW，LabVIEW 也不需要知道。

### 5.3 `snapshot`

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `format` | string | 是 | `jpeg` 或 `png` |
| `quality` | number | 是 | 1–100，僅 `jpeg` 有效；`png` 請填 `0` |

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

1. `data.kind = "taken"`：本輪結果已可靠取得。
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

範例（取得結果）：
```json
{"protocol_version":"1.0","req_id":"9a7f...","device":"DFU-01","cmd":"check","status":"ok","error":"","message":"","elapsed_sec":312.4,"data":{"kind":"taken","state":"complete","reason":"reliable_complete","capacity":4,"results":[{"slot":1,"status":"PASS"},{"slot":2,"status":"FAIL"},{"slot":3,"status":"NOTEST"},{"slot":4,"status":"PASS"}]}}
```

範例（逾時）：
```json
{"protocol_version":"1.0","req_id":"9a80...","device":"DFU-01","cmd":"check","status":"timeout","error":"timeout","message":"no reliable result within 600 s","elapsed_sec":600.0,"data":{"kind":"waiting","state":"monitoring","reason":"monitoring","capacity":4,"results":[]}}
```

### 5.5 `input`（僅 DFU）

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `items` | array of object | 是 | 1 筆以上 |
| `items[].slot` | number | 是 | DFU 輸入位置，必須在該設備輸入 profile（4／7 格）範圍內，不可重複 |
| `items[].sn` | string | 是 | SN，1–64 字元；可含逗號、冒號 |

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `entered` | array of number | 實際完成輸入的位置 |

DFU 輸入 profile（4／7 格）只決定輸入視窗，與 `check` 的結果格數無關。

只有 Log Solution 在 `standby`，或 `complete` 且本輪結果已取用時才允許輸入；`monitoring`／`review` 時回 `log_solution_busy`，不輸入任何 SN。

### 5.6 `button`（僅 BT）

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `action` | string | 是 | 1.0 只有 `start_all` |

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `action` | string | 實際執行的動作 |

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
| `invalid_argument` | error | 指令欄位值不合法（slot 超出範圍、timeout 超出範圍等） | 修正程式 |
| `unsupported_command` | error | 站別不支援此指令 | 修正程式 |
| `log_solution_busy` | error | `input` 時 Log Solution 為 `monitoring` 或 `review`（上一輪未結束或待確認） | 先完成本輪 `check`／人工確認 |
| `device_busy` | error | 同設備已有上鎖指令執行中 | 稍後重試，勿並行送 |
| `kvm_connect_failed` | error | 無法連線該設備的 JetKVM | 設備／網路異常，暫停 |
| `no_frame` | error | 尚未收到畫面 | 稍後重試 |
| `frame_frozen` | error | 畫面時間戳不再前進（ADR-0005） | 設備異常，暫停，不得判產品 FAIL |
| `hid_failed` | error | 鍵盤／滑鼠操作失敗或視窗定位失敗 | 設備異常，暫停 |
| `timeout` | timeout | `check` 長輪詢逾時 | 依流程決定是否再 `check` |
| `server_shutting_down` | error | Core 正在關閉 | 重連後重試 |
| `internal_error` | error | Core 內部例外 | 記錄並通報 |

## 7. 連線中斷

- LabVIEW 在長輪詢中斷線時，Core 取消該 `check` 並釋放設備鎖，不回覆。
- 重連後重新送 `check`。結果重傳、ACK 與重啟恢復屬 1.1。

## 8. LabVIEW typedef 清單（建議）

| typedef | 內容 |
| --- | --- |
| `Envelope` | 第 3 節除 `data` 外欄位 |
| `PingResp` | Envelope + `data{pong, core_version}` |
| `DevicesResp` | Envelope + `data{devices[]}` |
| `SnapshotResp` | Envelope + `data{width, height, format, image_base64}` |
| `CheckResp` | Envelope + `data{kind, state, reason, capacity, results[]{slot, status}}` |
| `InputResp` | Envelope + `data{entered[]}` |
| `ButtonResp` | Envelope + `data{action}` |

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

### 9.2 `devices`

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0002","device":"","cmd":"devices"}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0002","device":"","cmd":"devices",
 "status":"ok","error":"","message":"","elapsed_sec":0.0,
 "data":{"devices":[
   {"device":"DFU-01","station":"DFU","connected":true,"busy":false,"frozen":false},
   {"device":"FCT-01","station":"FCT","connected":false,"busy":false,"frozen":false},
   {"device":"BT-01","station":"BT","connected":true,"busy":true,"frozen":false}]}}
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

### 9.5 `button`（BT）

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0006","device":"BT-01","cmd":"button","action":"start_all"}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0006","device":"BT-01","cmd":"button",
 "status":"ok","error":"","message":"","elapsed_sec":1.2,
 "data":{"action":"start_all"}}
```

### 9.6 `check`

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

### 9.7 常見錯誤

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

## 10. 與現行程式的落差（實作待辦）

| 項目 | 現行 | 1.0 目標 |
| --- | --- | --- |
| 設備身份 | `device="1"`，三站同號互鎖；`kvm_ip` 由請求帶入 | 設備清單 `devices.json`，只帶設備 ID |
| `req_id` | 缺少時 Core 自動補 | 必填，缺少回錯誤 |
| `protocol_version`、`unknown_field` 檢查 | 無 | 有 |
| 回覆欄位 | 依指令與分支不同，部分省略、含 `null` | 固定 envelope + 固定 `data` |
| `input`／`button` | Core JSON 未註冊，只有舊 GUI 流程 | 註冊為正式指令 |
| `check` 長輪詢 | 待確認（paused）不立即回，等到逾時；非長輪詢在等待中回 `result=PASS` | paused 立即回；移除總結果欄位 |
| FCT／BT `check` | 模板判讀 | Log Solution 顯示契約判讀 |
| `stations`／`status` | 回傳 `kvm_ip`、內部狀態 | LabVIEW 改用 `devices`；`status`、`shutdown` 只供 Monitor UI／管理工具 |
