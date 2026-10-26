# LabVIEW ↔ JetKVM Log 橋接程式 TCP JSON 介面規格

版本：`protocol_version = "1.0"`
日期：2026-10-19（Asia/Taipei）
狀態：已在 Core Service 實作（spec #40 的 #41～#47）。LabVIEW 端的 type definition 與 VI 改版仍待 #13（人工）。實作對照與待辦見第 10 節。
相關：`CONTEXT.md`、ADR-0002／0003／0006、`docs/LABVIEW_HANDOFF.md`、`docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md`。

輪次 UUID、結果保存、接收確認（ACK）不在 1.0 範圍，定案後以 1.1 加入。`check` 已移除，不留別名；送出 `check` 回 `unknown_command`。

2026-10-19 使用者決定的三點補充（本輪開始請求、判讀順序、DFU 輸入）已納入第 5.4、5.5、5.6 節，與程式一致。

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
| `data` | object | 該指令固定欄位的物件；**錯誤或逾時時仍會出現**。欄位未取得時為預設值；`check_start`／`check_finish`／`inspect` 的逾時與部分錯誤會帶最後一次觀察值（見 5.4） |

LabVIEW 建議做法：以送出的 `cmd` 決定用哪一個完整 typedef（envelope + 該指令的 `data`）做 Unflatten From JSON，再核對 `req_id`、`device`、`cmd` 三者與請求相同後才採用。

## 4. 指令一覽

| `cmd` | 用途 | 上鎖 | DFU | FCT | BT |
| --- | --- | --- | --- | --- | --- |
| `ping` | 確認 Core 存活 | 否 | — | — | — |
| `status` | 取得 Core 與各設備連線狀態（供 LabVIEW 人機畫面顯示） | 否 | — | — | — |
| `snapshot` | 取一張當前畫面 | 否 | ✔ | ✔ | ✔ |
| `monitor_start` | 喚起 Log Solution 並確認進入監控中，建立本輪開始請求 | 是 | ✔ | ✔ | ✔ |
| `start_test` | 開始測試：DFU 點 OK、BT 點 Start All | 是 | ✔ | ✘ | ✔ |
| `check_start` | 測試開始確認：活動通道，或直接交付已完成的本輪 | 是 | ✔ | ✔ | ✔ |
| `check_finish` | 測試結束確認：取得本輪完整結果 | 是 | ✔ | ✔ | ✔ |
| `inspect` | Monitor UI 唯讀檢視（僅供 Monitor UI，LabVIEW 不得使用） | 否 | ✔ | ✔ | ✔ |
| `input` | 勾選通道並輸入本輪 SN（不點 OK、不開始監控） | 是 | ✔ | ✘ | ✘ |

- 「上鎖」指令在同一台設備已有上鎖指令執行中時，立刻回 `device_busy`（ADR-0002 fail-fast）。不同設備互不影響。`inspect` 不上鎖。
- 站別不支援的指令回 `unsupported_command`（例如 FCT 的 `start_test`、DFU 以外的 `input`）。
- `button` 與 `check` 已移除。送出回 `unknown_command`。
- 三站的 `check_start`／`check_finish`／`inspect` 一律以 Log Solution 顯示契約判讀，站別只影響操作指令。三者共用同一個 `data`。
- 只支援**顯示契約 1.3**（Log Solution `kvm_display_contract.CONTRACT_VERSION = "1.3"`）：左定位 (0, 2)、右定位 (320, 2)、狀態標記 (250, 0)、心跳 (278, 0)、色帶首排 y=34。定位點距離不是 320 的畫面（例如 1.1 的 238）一律判不可見，不讀 1.1／1.2。橋接與 Log Solution 1.3 必須同時部署。比例由定位點實際大小換算；定位點換算比例小於 0.70 倍判不可見（既有下限）。標記格沒有 12 px 檢查，1 倍輸出時小格為 10 px 仍照常判讀（使用者 2026-10-20 裁決），現場以 `docs/FIELD_VERIFICATION.md` L1 確認。

### 4.1 三站呼叫順序

先監控、後開測。前一步回 `status = "ok"` 才進行下一步；`error` 依第 6 節處理。

| 站別 | 呼叫順序 |
| --- | --- |
| DFU | `input` → `monitor_start` → `start_test` → `check_start`（可省略）→ `check_finish` |
| BT | `monitor_start` → `start_test` → `check_start`（可省略）→ `check_finish` |
| FCT | 治具推入前設備發訊號 → `monitor_start` → `check_start`（可省略）→ `check_finish` |

- FCT 沒有 `start_test`。治具推入後由 ATE 自行開測，LabVIEW 必須在推入前完成 `monitor_start`。
- `monitor_start` 成功只代表 Log Solution 已準備好收集本輪資料，不代表 ATE 已開始測試。每一輪都要重新 `monitor_start`。
- `check_start` 確認 ATE 真的開始測試，並回已開始的通道。可省略、可重複；重複呼叫不消耗結果。
- `check_finish` 取得整輪結果。它不回 `started`。
- 只有 `kind = "taken"` 可以觸發設備動作。`already_taken` 附相同結果，只供重看。

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
| `devices[].frozen` | boolean | 畫面判定凍結後持續為 true，直到讀取影格的指令觀察到同一設備 PTS 前進才解除（見下方說明），不需人工確認 |
| `devices[].channels` | number | 設定的測試通道數 |

凍結 latch 保存在記憶體，Core 重啟會清除，但不代表設備已修復。

凍結解除說明：只有讀取影格的指令（`check_start`、`check_finish`、`inspect`、`input`、`start_test`、`monitor_start`）能觀察 PTS 前進並解除凍結；`status`／`core_status` 不讀取影格，持續輪詢也不會解除。KVM 重連換串流後至少需讀取兩次：首張建立基準，後續影格須有 PTS 前進。LabVIEW 不應只輪詢 `status` 等待 `frozen=false`，應送唯讀的 `inspect` 重新判斷，必要時重複送出。

KVM 斷線不會解除凍結。唯一例外範圍很窄：某次讀取判定 FROZEN，而該次讀取的回覆是 `kvm_connect_failed`（不是 `frame_frozen`；包括讀取超過 inspection timeout、在背景完成時 KVM 已斷線的那次讀取），只撤銷這個判定剛新增的凍結 latch。理由：LabVIEW 從未因這次讀取收到 `frame_frozen`，而且這個凍結判定可能只是 KVM 在讀取途中斷線造成的假象。同一 latch 一旦有任何命令已向 LabVIEW 回覆 `frame_frozen`，就不再適用此例外，即使建立 latch 的原讀取後來回覆 `kvm_connect_failed` 也必須保留，直到 PTS 前進。斷線前就已存在的凍結 latch 一律保留。已鎖定凍結的設備後續 KVM 斷線時，依使用者決定（2026-10-26）繼續顯示凍結：`status` 維持 `frozen=true`、`connected=false`，`core_status` 的 `stations[].status` 為 `FROZEN`、`is_connected=false`；Monitor UI 該列顯示紅色 Frame frozen，目前 tooltip 只顯示凍結文字，不顯示 KVM 連線狀態；加入連線狀態須待面板 PR #137 合併後另開後續票，本 PR 不修改 UI。凍結只在畫面重新前進（PTS 前進）時解除，目的是讓人看得出設備在斷線前已經異常。

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

### 5.4 `check_start`／`check_finish`／`inspect` 共用回覆

三者的專屬請求欄位相同：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `timeout_sec` | number | 是 | `0` = 只讀一次立即回覆；`> 0` = 長輪詢，最多 `3600`。缺少、布林或超出範圍回 `invalid_argument` |

`timeout_sec` 白話：LabVIEW 願意等多久。LabVIEW 的 TCP Read timeout 必須**大於** `timeout_sec`（建議多 30 秒）。

長輪詢在下列任一情況**立即回覆**，否則等到逾時：

1. `kind = "taken"`（僅 `check_start`／`check_finish`）。
2. `kind = "paused"`。
3. `kind = "already_taken"`。
4. `kind = "started"`（僅 `check_start`／`inspect`）。
5. 任何錯誤（例如 `frame_frozen`、`monitor_not_ready`、`manual_verification_required`）。

逾時回覆 `status = "timeout"`、`error = "timeout"`，`data` 帶最後一次觀察到的狀態。

`data` 固定欄位，無 `null`、不省略：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `kind` | string | `started`／`taken`／`already_taken`／`waiting`／`paused`／`unknown`。錯誤回覆尚未觀察到畫面時也是 `unknown`（不是空字串） |
| `state` | string | `standby`／`preparing`／`monitoring`／`review`／`complete`／`manual_stop_complete`；無法辨識為 `""` |
| `reason` | string | 細部原因，僅供記錄。LabVIEW 只依 `kind` 與 `error` 分支 |
| `capacity` | number | 畫面結果格數 1–20；無法辨識為 `0` |
| `active` | array of object | 已開始通道。`check_finish` 固定 `[]`。其餘見下 |
| `active[].slot` | number | 通道 1–20 |
| `active[].evidence` | string | `testing` 或 `result` |
| `results` | array of object | 僅 `taken` 與 `already_taken` 有內容，其餘為 `[]` |
| `results[].slot` | number | 1–20；第一排左至右 1–10，第二排 11–20 |
| `results[].status` | string | `PASS`／`FAIL`／`NOTEST`／`TIMEOUT`／`STOPPED` |

`kind` 與 LabVIEW 動作：

| `kind` | 出現在 | 意義 | `results` | LabVIEW 動作 |
| --- | --- | --- | --- | --- |
| `started` | `check_start`、`inspect` | 連續兩張新鮮影格確認有通道開始。`reason` 為 `activity_confirmed` | `[]` | 記錄 `active`，接著 `check_finish`。`inspect` 只顯示，不做設備動作 |
| `taken` | `check_start`、`check_finish` | 本輪完整結果**第一次**交付 | 完整 | **唯一**可做設備動作的回覆。PASS 良品；FAIL／NOTEST／TIMEOUT／STOPPED 不良。`state = "manual_stop_complete"` 標記人工停止並交作業員重工 |
| `already_taken` | `check_start`、`check_finish` | 同一輪再次交付，結果與第一次相同 | 與第一次相同 | 只顯示、核對，不再做設備動作 |
| `waiting` | 三者 | 還沒有可交付結果 | `[]` | 繼續等或再查 |
| `paused` | 三者 | 畫面是待確認（`state = "review"`） | `[]` | 暫停設備動作，等人員處理後再查 |
| `unknown` | 三者 | 畫面看不清楚 | `[]` | 設備／通訊異常，不得判產品 FAIL |

`inspect` 不會回 `taken` 或 `already_taken`。

六種整輪圖樣（讀取順序左上、右上、左下、右下，1 = 黑）。其他圖樣、缺格、低對比為 unknown：

| `state` | 圖樣 | 中文 |
| --- | --- | --- |
| `standby` | 1001 | 待命 |
| `preparing` | 0101 | 準備中 |
| `monitoring` | 1100 | 監控就緒 |
| `review` | 1010 | 待確認 |
| `complete` | 0110 | 一般完成 |
| `manual_stop_complete` | 0011 | 人工停止完成 |

完成種類（一般／人工停止）納入穩定簽章：完成種類＋格數＋每格結果，兩張相同才交付。種類切換重新計數。一般完成與人工停止完成都可交付；人工停止即使每一格都已是終局、沒有 STOPPED，仍以 `state = "manual_stop_complete"` 交付。

`active` 的證據：TESTING／COMPLETING 為 `testing`；未見測試中而看到 PASS／FAIL 為 `result`。NOTEST／TIMEOUT／STOPPED／WAITING 不算活動。一個通道要在連續兩張新鮮影格（序號前進、接收間隔 ≤ 1 秒）都有活動才列入；兩張裡任一張是測試中，證據為 `testing`。中間出現看不清楚的畫面就重新計算。

#### 判讀順序

`check_start` 與 `check_finish` 依下列順序，先符合的就回（2026-10-19 決定）：

1. 畫面是待確認（REVIEW）→ `kind = "paused"`。
2. 畫面簽章和上一次交付完全相同（完成種類＋格數＋每格結果），而且之後沒有出現新一輪的信號 → `kind = "already_taken"`，附相同 `results`。這條排在本輪開始請求檢查之前，所以交付後即使請求已清除，也可以重看。
3. 畫面看不清楚 → `kind = "unknown"`。可見性恢復仍適用。
4. 沒有本輪開始請求 → `error = "monitor_not_ready"`，`results` 為 `[]`。
5. 有請求但沒看到本次監控中，且畫面已可交付 → `error = "manual_verification_required"`，不交付、不點 Dock。此回覆清除本輪開始請求。
6. 有請求且已見本次監控中 → 正常判讀：兩張穩定的可交付畫面回 `taken`（並清除請求）；`check_start` 的活動通道回 `started`（不清除請求）；否則 `waiting`。

因此，交付後簽章變了、沒有新一輪的信號、請求已清除時，依第 4 步回 `monitor_not_ready`，不會再交付一次。

新一輪的信號是看到準備中或監控中，或收到新的 `monitor_start`。出現任一個，就清掉「上一次交付的簽章」。準備中本身不建立本輪開始請求；只有 `monitor_start` 建立。

`check_start` 在沒有本輪開始請求時，即使已有兩張活動影格，也回 `monitor_not_ready`，不回 `started`。有請求時，可交付畫面優先於 `started`：兩張穩定完成畫面回 `taken`。重複 `check_start` 看到活動通道會一直回 `started`，不消耗本輪，之後的 `check_finish` 仍可 `taken`。

格數與設備通道數不同的可交付畫面回 `capacity_mismatch`，不交付、不清除本輪開始請求。

#### 可見性與遺失

套用於 `check_start`、`check_finish`。`inspect` 不點 Dock。

- **Log Solution 不可見**：`kind = unknown` 且原因不屬於畫面／串流問題。畫面／串流問題（`no_frame`、`invalid_frame`、`stale_frame`、`missing_source_frame_time`、`out_of_order_source_frame`、`duplicate_or_out_of_order_frame`、`inspection_busy`、`inspection_timeout`）不算看不到，也不歸零計時。
- 準備中、待命、監控中、待確認、一般完成、人工停止完成都是可靠讀數，會歸零計時。準備中不會被當成看不到而去點 Dock。
- 連續 30 秒看不到：點一次 Log Solution 的 Dock 圖示，滑鼠移到中性位置，等 5 秒後重新判讀。同一請求最多 3 次，仍看不到回 `log_solution_not_visible`。
- Dock 上找不到圖示：立即回 `log_solution_not_found`，不算一次恢復。
- 本輪已見監控中之後回到待命：立即回 `round_lost`。
- 心跳讀不到（可靠讀數但心跳任一格讀不到）：`kind = unknown`、`reason = heartbeat_unreadable`，屬 Log Solution 看不到，走上述 Dock 補救。

#### Log Solution 心跳停止（issue #77）

Log Solution 在狀態標記右側有 2×2 黑白心跳方塊（顯示契約 1.3，每 0.5 秒 +1，App 活著就一直跳）。Mac 或 Log Solution 當機時 HDMI 仍送出同一張畫面、PTS 仍前進，所以只看 PTS 無法判斷。橋接只比對四格黑白是否改變，不解讀計數值。

- 計入序列的觀察：新影格（序號不同）、四格可讀、與上一張已計入者的接收時間相隔 0.8～3 秒。相隔不到 0.8 秒的觀察不計入、不清空；相隔超過 3 秒，序列重新開始。
- 任一無效觀察（四格有讀不到、影格過舊、串流凍結、換 stream、定位失敗）清空序列。
- 連續 3 張有效觀察四格相同 → `log_solution_heartbeat_stopped`，message「畫面心跳停止，請人員檢查測試電腦與 Log Solution」。正常時約 2 秒內判定。這不宣稱 App 已當機。
- 心跳讀不到不是心跳停止：可靠讀數但四格有讀不到，或定位失敗，一律屬 Log Solution 看不到，走 Dock 補救，不回 `log_solution_heartbeat_stopped`。
- 不鎖定、沒有人工復位指令：之後四格再變化即恢復正常。橋接不自動重試、不重新開測、不取用不確定結果。
- 判讀順序：KVM 健康 → 串流凍結（`frame_frozen`）→ 心跳 → 判讀標記與色帶。`check_start`、`check_finish`、`inspect` 長輪詢中途看到心跳停止就立即回覆，不等 `timeout_sec`。
- `input`、`start_test`、`monitor_start` 送 HID 前（接在凍結檢查之後）：最近 3 秒內見過有效變化即放行；否則主動取樣最多約 2.5 秒。見變化放行；3 張同圖回 `log_solution_heartbeat_stopped`；取樣時間內判不出（讀不到或沒有新影格）則交給各指令既有的流程（`hid_failed`、`monitor_not_ready`、Dock 補救）。三者回此碼時都不送 HID。
- 已知限制：心跳剛停止、距上一次有效變化未滿 3 秒時，送 HID 前的檢查會放行。
- `monitor_start` 等待監控中時心跳停止，立即回 `log_solution_heartbeat_stopped`，保留已建立的本輪開始請求（同等待中的 `frame_frozen`）。
- `data` 沿用各指令既有預設形狀。

### 5.5 `input`（僅 DFU）

流程：前景化 DFU 視窗（點 DFU Dock 圖示）→ 勾選請求的通道、取消其餘 → 逐筆輸入 SN。**不點 OK、不送 Cmd+Shift+M**。

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `items` | array of object | 是 | 1 筆以上 |
| `items[].slot` | number | 是 | 測試通道編號 1～N，不可重複 |
| `items[].sn` | string | 是 | SN，1–64 字元；可含逗號、冒號 |

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `entered` | array of number | 實際完成輸入的位置 |

送 HID 前先做畫面凍結檢查（同 5.6 第 0 步）：KVM 已斷線回 `kvm_connect_failed`，凍結回 `frame_frozen`，兩者都不送 HID。接著做心跳檢查，心跳停止回 `log_solution_heartbeat_stopped`，同樣不送 HID（見 5.4「Log Solution 心跳停止」）。

是否允許輸入（2026-10-19 決定；一般完成與人工停止完成都屬 READY）：

| Log 狀態 | 結果 |
| --- | --- |
| 畫面凍結（PTS 未前進，或無 PTS） | `frame_frozen`，不送 HID |
| 待命 | 可以輸入 |
| 一般完成或人工停止完成，且本輪已 `taken` 或已回過 `manual_verification_required` | 可以輸入下一輪 SN |
| 一般完成或人工停止完成，但本輪尚未交付、也沒回過 `manual_verification_required` | `log_solution_busy`，不送 HID |
| 監控中、準備中、待確認 | `log_solution_busy`，不送 HID |
| 沒有影格或看不清楚 | `hid_failed`，不送 HID |

### 5.6 `monitor_start`（DFU、FCT、BT）

專屬請求欄位：

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `timeout_sec` | number | 是 | 等候監控中的秒數，1–300。缺少、布林或超出範圍回 `invalid_argument`，不送 HID |

上鎖。收到合法的 `monitor_start` 即視為新一輪信號，清掉上一次交付簽章。等候預算是這次請求的 `timeout_sec`，不是固定 10 秒。

流程（依序，任一步失敗即停止）：

0. 畫面凍結檢查（`input`、`start_test`、`monitor_start` 共用，ADR-0005 2026-10-20 第 3 點）：KVM 已斷線 → `kvm_connect_failed`；畫面凍結 → `frame_frozen`。不送 HID，不建立本輪開始請求。凍結判定是主動窗口：最近 1 秒內 PTS 有前進就直接放行，否則最多等 2 秒讀到 PTS 前進。窗口內 KVM 死亡回 `kvm_connect_failed`。檢查只做一次，之後的點擊不再逐次檢查。沒有影格不算凍結，仍依下列各步回 `hid_failed`。心跳檢查（issue #77）接在凍結檢查之後，心跳停止回 `log_solution_heartbeat_stopped`，不送 HID、不建立本輪開始請求；規則見 5.4。
1. 沒有影格 → `hid_failed`。
2. 已在監控中 → `log_solution_busy`，不送 HID，也不建立新的本輪開始請求。
3. 待確認 → `log_solution_review`，不送 HID。
4. 準備中 → 不點 Dock、不送快捷鍵，直接等監控中。這一張準備中就建立（或保留）該設備的本輪開始請求。
5. 其餘（待命、一般完成、人工停止完成，或當時看不清楚）：點共用 Dock 圖示 `COMMON/COMMON_log_solution_dock_icon.png`。模板缺失或 HID 錯誤 → `hid_failed`；圖示不在畫面上 → `log_solution_not_found`。
6. 點擊後 3 秒內須讀到可靠畫面，否則 `log_solution_not_found`。讀到監控中 → `log_solution_busy`；待確認 → `log_solution_review`；準備中則不送快捷鍵、改為等待。待命、一般完成、人工停止完成才送 Cmd+Shift+M，並建立本輪開始請求。
7. 在 `timeout_sec` 內：準備中繼續等（並保留請求）；待確認立即 `log_solution_review`；看到監控中即成功。這一次呼叫中看到監控中，視為「已見本次監控就緒」。等待中畫面凍結 → 立即 `frame_frozen`，不等到 `timeout_sec`（保留已建立的請求）；等待中 KVM 斷線 → 立即 `kvm_connect_failed`，凍結時也先查斷線。逾時回 `monitor_not_started`。

`monitor_not_started` 不清除本輪開始請求。只有 `taken` 或 `manual_verification_required` 才清除。因此準備逾時之後，若畫面變成可交付結果，`check_finish` 回 `manual_verification_required`，不交付。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `state` | string | 成功時為 `monitoring`；失敗為 `""` |
| `capacity` | number | 成功時畫面上的結果格數；失敗為 `0` |

### 5.7 `start_test`（DFU、BT）

無專屬請求欄位。上鎖。FCT 回 `unsupported_command`。

流程：

0. 畫面凍結檢查（同 5.6 第 0 步）：KVM 已斷線 → `kvm_connect_failed`；畫面凍結 → `frame_frozen`，不送 HID。心跳檢查接在其後：心跳停止 → `log_solution_heartbeat_stopped`，不送 HID。
1. 讀取最新畫面。不是監控中（含準備中、待命、待確認、完成、看不清楚、沒有影格）→ `monitor_not_ready`，不送 HID。
2. DFU：點 DFU Dock 圖示，找到視窗與 OK 後點一次。BT：點 BT Dock 圖示，找到 Start All 後點一次。
3. 模板找不到或 HID 錯誤 → `hid_failed`。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `action` | string | 成功時 DFU 為 `ok`、BT 為 `start_all`；失敗為 `""` |

### 5.8 `inspect`（僅供 Monitor UI）

欄位與共用 `data` 同 5.4。Monitor UI 手動檢查送這個指令，並把 `state` 顯示成中文（待命、準備中、監控就緒、待確認、一般完成、人工停止完成），`active` 顯示成「通道 N 測試中」或「通道 N 已有結果」。

- 不上鎖，不回 `device_busy`。
- 唯讀：不點 Dock、不消耗本輪、不套用本輪開始請求（因此不回 `monitor_not_ready`／`manual_verification_required`／`taken`）。
- 兩張新鮮影格確認活動通道時回 `started`，否則依畫面回 `waiting`／`paused`／`unknown`。
- LabVIEW 不得使用。

### 5.9 `core_status`（僅供 Monitor UI 與 `manage_core.py`，非 LabVIEW 協定）

請求只有共同欄位，`device` 填 `""`。不在 4 節一覽表內，LabVIEW 不得使用。

`data`：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `status` | string | 固定 `"running"` |
| `core_version` | string | Core Service 版本，與 `ping.core_version` 相同（`CORE_VERSION`） |
| `uptime_seconds` | number | Core 已運行秒數 |
| `active_connections` | number | 目前 TCP 連線數 |
| `busy_devices` | array of string | 上鎖中的設備 ID |
| `kvm_pool` | object | `active_connections`、`cached_ips` |
| `freeze_guard` | object | 凍結檢查狀態 |
| `stations` | array of object | 各設備的 `station`、`device`、`kvm_ip`、`status`（`BUSY`／`FROZEN`／`IDLE`／`DISCONNECTED`）等 |
| `stations[].frame_size` | array or null | 最近一次讀取的影格 `[寬, 高]`（像素）。尚無讀取，或設備未連線（`is_connected` 為 `false`）時為 `null` |
| `stations[].picture_scale` | number or null | 最近一次讀取換算的比例（Log Solution 1.3 基準單位→像素），四捨五入到 0.001。最近一次讀取未定位（無法換算）或尚無讀取為 `null` |
| `stations[].cell_px` | number or null | 標記小格像素，`picture_scale` × 10，四捨五入到 0.001。`picture_scale` 為 `null` 時也為 `null` |
| `stations[].last_reading` | object or null | 最近一次讀取：`reason`（可靠時為 `""`，否則為實際不可見原因，例如 `locators_missing_or_ambiguous`）、`age_sec`（距今秒數）。尚無讀取為 `null` |
| `stations[].readable_ratio` | object | `ratio`：最近 50 次讀取中可靠的比例（0–1，四捨五入到 0.001），無樣本為 `null`；`samples`：樣本數（≤ 50） |

上列五欄位為讀取時累積的統計，`core_status` 本身不讀畫面、不拿設備鎖，因此未讀取過的設備一律為 `null`／0 樣本。每次解碼一張影格記一次，包括 check、inspect、monitor_start、HID 前拒絕判斷與心跳取樣。LabVIEW 的 `status` 不回傳這些欄位。

變更紀錄：版本欄位原為 `version`（固定 `"1.0.0"`），改為 `core_version`（`CORE_VERSION`，即 `ping` 回的版本）。

## 6. 錯誤碼

| `error` | `status` | 何時發生 | LabVIEW 建議 |
| --- | --- | --- | --- |
| `invalid_json` | error | 該行不是合法 JSON | 修正程式 |
| `invalid_request` | error | 不是 JSON 物件，或欄位型別錯誤 | 修正程式 |
| `unsupported_protocol_version` | error | `protocol_version` 不是 `1.0` | 修正程式 |
| `missing_req_id` | error | 未帶 `req_id` 或為空 | 修正程式 |
| `missing_command` | error | 未帶 `cmd` | 修正程式 |
| `unknown_command` | error | `cmd` 不存在（含已移除的 `check`、`button`） | 修正程式 |
| `missing_device` | error | 需要設備的指令 `device` 為空 | 修正程式 |
| `unknown_device` | error | `device` 不在設備清單 | 檢查設備 ID |
| `unknown_field` | error | 帶了未定義欄位 | 修正程式 |
| `forbidden_field` | error | 帶了 `kvm_ip` 或 `station` | 修正程式 |
| `invalid_argument` | error | 欄位值不合法。`check_start`／`check_finish`／`inspect` 的 `timeout_sec` 須為 0–3600；`monitor_start` 須為 1–300 | 修正程式 |
| `unsupported_command` | error | 站別不支援此指令 | 修正程式 |
| `log_solution_busy` | error | `monitor_start` 時已在監控中；或 `input` 時為監控中／準備中／待確認，或完成（含人工停止完成）但本輪尚未交付也沒回過 `manual_verification_required` | 先完成本輪或等人工核對 |
| `log_solution_review` | error | `monitor_start` 時畫面為待確認 | 報警、暫停；人員排除並復位後才能再 `monitor_start` |
| `capacity_mismatch` | error | 可交付畫面的格數與設定通道數不同 | 檢查 Log Solution 設定，暫停 |
| `device_busy` | error | 同設備已有上鎖指令執行中 | 稍後重試，勿並行送 |
| `kvm_connect_failed` | error | 無法連線該設備的 JetKVM；或**指令進行中 KVM 斷線**（影像軌道結束，或 peer connection 變成 `failed`／`closed`，#58）。死亡訊號出現時立即回覆，包括 Freeze Guard 等待期間。拔線這類情況要等 ICE 逾時（數十秒）才會出現死亡訊號，在這之前仍可能回 `frame_frozen`。畫面靜止本身不是斷線 | 設備／網路異常，暫停。KVM 已確定死亡；下一個指令會自動重連，不要只重試畫面 |
| `no_frame` | error | 尚未收到畫面 | 稍後重試 |
| `frame_frozen` | error | 畫面時間戳不再前進，或畫面沒有 PTS（ADR-0005）。`input`、`start_test`、`monitor_start` 送 HID 前檢查，`monitor_start` 等待中也會檢查；這三個指令回此碼時未送出任何 HID | 設備異常，暫停，不得判產品 FAIL |
| `log_solution_heartbeat_stopped` | error | Log Solution 畫面心跳停止：連續 3 張有效觀察的心跳四格相同（約 2 秒內判定）。`check_start`／`check_finish`／`inspect` 長輪詢中途回覆；`input`、`start_test`、`monitor_start` 送 HID 前或等待中回覆，這三個指令回此碼時未送出任何 HID。message「畫面心跳停止，請人員檢查測試電腦與 Log Solution」。不鎖定，心跳再變化即恢復 | 暫停，不得判產品 FAIL；不自動重試、不重新開測。人員檢查測試電腦與 Log Solution |
| `hid_failed` | error | 鍵盤／滑鼠操作失敗、視窗或按鈕定位失敗、模板缺失；`input`／`monitor_start` 沒有影格或狀態看不清楚 | 設備異常，暫停 |
| `log_solution_not_found` | error | Dock 上找不到 Log Solution，或點擊後 3 秒內找不到視窗 | 停線檢查 |
| `monitor_not_started` | error | `monitor_start` 在 `timeout_sec` 內沒看到監控中。不清除本輪開始請求 | 暫停並通知人員 |
| `monitor_not_ready` | error | `check_start`／`check_finish` 沒有本輪開始請求（含交付後簽章已變、又沒有新一輪信號）；或 `start_test` 時不是監控中 | 先完成本輪的 `monitor_start` |
| `log_solution_not_visible` | error | `check_start`／`check_finish` 連續 30 秒看不到 Log Solution，點 Dock 3 次仍看不到 | 暫停，不得判產品 FAIL |
| `manual_verification_required` | error | 有本輪開始請求，但沒看到本次監控中，畫面卻已可交付（準備逾時、或快捷鍵後直接人工停止） | 不交付、不自動重測；暫停並請人員核對。此回覆清除本輪開始請求 |
| `round_lost` | error | 本輪已見監控中之後 Log Solution 回到待命 | 本輪結果已無法取得，暫停 |
| `timeout` | timeout | `check_start`／`check_finish`／`inspect` 長輪詢逾時 | 依 `data.state` 決定再查或交人工 |
| `server_shutting_down` | error | Core 正在關閉 | 重連後重試 |
| `internal_error` | error | Core 內部例外 | 記錄並通報 |

以上錯誤都不是產品結果，不得據此判產品 FAIL。

## 7. 連線中斷

- LabVIEW 在長輪詢中斷線時，Core 取消該請求並釋放設備鎖，不回覆。本輪開始請求留在 Core 存活期間，不因這次逾時或斷線清除。
- 重連後重新送原指令。結果重傳、ACK 與重啟恢復屬 1.1。同一輪在簽章未變時，重送 `check_finish` 得到 `already_taken` 並附原結果；此時本輪開始請求已因交付結清，不需仍在。KVM 重連（stream_id 改變）不影響這個重問：交付紀錄保留，但重連後須重新見到 Monitoring 才能取用新結果，重連前已 armed 而尚未交付的輪次，取得結果時仍回 `manual_verification_required`。範圍限同一 Core 行程內。

## 8. LabVIEW typedef 清單（建議）

| typedef | 內容 |
| --- | --- |
| `Envelope` | 第 3 節除 `data` 外欄位 |
| `PingResp` | Envelope + `data{pong, core_version}` |
| `StatusResp` | Envelope + `data{core_uptime_sec, devices[]}` |
| `SnapshotResp` | Envelope + `data{width, height, format, image_base64}` |
| `CheckStartResp` | Envelope + `data{kind, state, reason, capacity, active[]{slot, evidence}, results[]{slot, status}}` |
| `CheckFinishResp` | 與 `CheckStartResp` 相同；`active` 成功時為空陣列，且不會出現 `kind = "started"` |
| `InspectResp` | 與 `CheckStartResp` 相同；不會出現 `taken`／`already_taken`。僅 Monitor UI |
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

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0004","device":"DFU-01","cmd":"input",
 "items":[{"slot":1,"sn":"F5K2-001"},{"slot":3,"sn":"F5K2:003,B"}]}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0004","device":"DFU-01","cmd":"input",
 "status":"ok","error":"","message":"","elapsed_sec":8.7,
 "data":{"entered":[1,3]}}
```

Log Solution 仍在準備中時：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0005","device":"DFU-01","cmd":"input",
 "status":"error","error":"log_solution_busy","message":"Log Solution state is preparing","elapsed_sec":0.3,
 "data":{"entered":[]}}
```

### 9.5 `monitor_start`

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0006","device":"DFU-01","cmd":"monitor_start","timeout_sec":30}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0006","device":"DFU-01","cmd":"monitor_start",
 "status":"ok","error":"","message":"","elapsed_sec":4.2,
 "data":{"state":"monitoring","capacity":4}}
```

開始前已是待確認：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0013","device":"DFU-01","cmd":"monitor_start",
 "status":"error","error":"log_solution_review","message":"Log Solution 為待確認（開始前），未繼續開始監控","elapsed_sec":0.2,
 "data":{"state":"","capacity":0}}
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

### 9.7 `check_start` 與 `check_finish`

`check_start` 確認通道已開始（`check_finish` 不回這個 `kind`，`active` 為 `[]`）：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0007","device":"DFU-01","cmd":"check_start","timeout_sec":0}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0007","device":"DFU-01","cmd":"check_start",
 "status":"ok","error":"","message":"","elapsed_sec":0.2,
 "data":{"kind":"started","state":"monitoring","reason":"activity_confirmed","capacity":4,
  "active":[{"slot":1,"evidence":"testing"},{"slot":2,"evidence":"result"}],"results":[]}}
```

`check_finish` 第一次交付：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0014","device":"DFU-01","cmd":"check_finish","timeout_sec":600}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0014","device":"DFU-01","cmd":"check_finish",
 "status":"ok","error":"","message":"","elapsed_sec":312.4,
 "data":{"kind":"taken","state":"complete","reason":"reliable_complete","capacity":4,"active":[],
  "results":[{"slot":1,"status":"PASS"},{"slot":2,"status":"FAIL"},{"slot":3,"status":"NOTEST"},{"slot":4,"status":"PASS"}]}}
```

同一簽章再查，附相同結果：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0015","device":"DFU-01","cmd":"check_finish",
 "status":"ok","error":"","message":"","elapsed_sec":0.2,
 "data":{"kind":"already_taken","state":"complete","reason":"round_already_taken","capacity":4,"active":[],
  "results":[{"slot":1,"status":"PASS"},{"slot":2,"status":"FAIL"},{"slot":3,"status":"NOTEST"},{"slot":4,"status":"PASS"}]}}
```

待確認立即回：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0008","device":"DFU-01","cmd":"check_finish",
 "status":"ok","error":"","message":"","elapsed_sec":145.3,
 "data":{"kind":"paused","state":"review","reason":"confirmation_required","capacity":4,"active":[],"results":[]}}
```

沒有本輪開始請求：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0016","device":"DFU-01","cmd":"check_finish",
 "status":"error","error":"monitor_not_ready","message":"no round-start request; result was not taken","elapsed_sec":0.1,
 "data":{"kind":"unknown","state":"complete","reason":"reliable_complete","capacity":4,"active":[],"results":[]}}
```

### 9.8 常見錯誤

同一台設備已有動作在執行：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0009","device":"DFU-01","cmd":"input",
 "status":"error","error":"device_busy","message":"DFU-01 is busy with req 6f1d0c2e-0008","elapsed_sec":0.0,
 "data":{"entered":[]}}
```

設備 ID 大小寫錯誤（`check_finish` 的錯誤 `data` 仍含 `active`）：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0010","device":"dfu-01","cmd":"check_finish",
 "status":"error","error":"unknown_device","message":"device 'dfu-01' is not configured","elapsed_sec":0.0,
 "data":{"kind":"unknown","state":"","reason":"","capacity":0,"active":[],"results":[]}}
```

帶了 `kvm_ip`：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0011","device":"DFU-01","cmd":"check_finish",
 "status":"error","error":"forbidden_field","message":"field 'kvm_ip' is not allowed","elapsed_sec":0.0,
 "data":{"kind":"unknown","state":"","reason":"","capacity":0,"active":[],"results":[]}}
```

已移除的 `check`：
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0017","device":"DFU-01","cmd":"check","timeout_sec":0}
```
```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0017","device":"DFU-01","cmd":"check",
 "status":"error","error":"unknown_command","message":"unknown command 'check'","elapsed_sec":0.0,
 "data":{}}
```

## 10. 實作對照與待辦

| 項目 | 1.0 規定 | 目前實作 |
| --- | --- | --- |
| 設備身份、`req_id`、`protocol_version`、固定回覆欄位 | 第 2、3 節 | 已實作 |
| `check` 移除，改 `check_start`／`check_finish` | 共用 `data`，含 `active`；協定維持 `1.0` | 已實作。`check` 回 `unknown_command` |
| 六種整輪圖樣 | 含準備中 0101、人工停止完成 0011 | 已實作 |
| `monitor_start` | 必填 `timeout_sec` 1–300；準備中等待；待確認回 `log_solution_review`；準備中或快捷鍵建立本輪開始請求 | 已實作 |
| 本輪開始請求 | 無請求回 `monitor_not_ready`；有請求未見監控中的可交付畫面回 `manual_verification_required`；`taken` 或該錯誤才清除 | 已實作（含 2026-10-19 三點補充） |
| `check_start` | 兩影格交集、`started`、完成畫面可直接 `taken`、重複不消耗 | 已實作。`reason` 為 `activity_confirmed` |
| 可見性 | 30 秒、5 秒、最多 3 次；準備中歸零計時；`inspect` 不點 Dock | 已實作 |
| DFU `input` | 待命可輸入；完成與人工停止完成在本輪已交付或已人工核對後可輸入 | 已實作 |
| Monitor UI | `inspect` 顯示中文狀態與已開始通道 | 已實作 |

### 仍待辦

- **LabVIEW 端**：typedef 與 VI 改版屬 #13，需人工在 LabVIEW 完成。本文件與 `docs/LABVIEW_HANDOFF.md` 是依據。
- **Log Solution 顯示契約 1.3（#32）**：本 PR（#93）已實作，程式只認 1.3。2026-10-20 決定橋接改為只支援 1.3（定位 (0,2)／(320,2)、狀態 (250,0)、心跳 (278,0)），不保留 1.1；橋接與 Log Solution 1.3 須同時部署。心跳判讀見 #77。
- **Log Solution 應用程式本身**：圖樣、準備中、視窗前景、FCT 期限由 Log Solution 專案實作。橋接只讀畫面。
- **FCT 推入前訊號的現場時序**：決定 Log Solution FCT profile 的 start 期限（建議先設 120 秒）。
- **JetKVM 靜態 IP**：正式版是否提供靜態 IP 設定，待現場確認（見 `docs/DEPLOYMENT.md`）。
- **協定 1.1**：輪次 UUID、ACK、重啟後重傳。不在本次範圍。
