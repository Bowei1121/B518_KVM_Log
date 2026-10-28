# Spec: KVM 連線與指令耗時紀錄（Core log＋Monitor UI 日誌區）

GitHub issue：https://github.com/Bowei1121/B518_KVM_Log/issues/154（本檔為發布時的本機副本）

子票：#155、#156、#157、#158。

相關文件：`CONTEXT.md`（KVM Connection Pool、連線失敗異常、設備 ID、設備清單、Monitor UI）、`docs/PROTOCOL.md` §5.9 `core_status`、ADR-0001（KVM 連線池）、ADR-0002（Device Lock fail-fast）、ADR-0004（Core 與 UI 分離）、ADR-0007（設備清單與統一設備 ID）、#134（連線失敗異常、Core 背景重連）、PR #153。保留上述 ADR；本 spec 不改連線失敗的計次規則，也不改 Log Solution 顯示契約。

用語：本 spec 的「心跳」一律指 **KVM Connection Pool 的心跳健康檢查**（Core 每 `heartbeat_interval` = 5 秒詢問每條池中連線是否仍健康），**不是**「Log Solution 心跳」（畫面上的 2×2 方塊）。

## Problem Statement

現場工程師與 RD 查問題時，看不到「一台 KVM 連線花了多久」與「一個指令從收到到回覆花了多久」：

- Core 背景重連（#134，單次最多 `connect_timeout_sec` = 10 秒）與指令路徑連線（LabVIEW／Monitor UI 指令到達時才建立連線）目前只寫「正在連線」「已建立」「失敗 (例外類別)」，沒有耗時，也沒有設備 ID，只能用 IP 對照設備清單。
- 連線失敗只記例外類別名稱（例如 `TimeoutError`、`RuntimeError`），看不出是逾時、被拒、登入失敗還是收不到畫面，也看不出失敗前等了多久。
- 指令的耗時只回在 LabVIEW 的回覆 envelope（`elapsed_sec`），Core log 沒有逐筆紀錄；沒有回覆（LabVIEW 斷線）的指令更是無從查起。
- KVM 心跳判定不健康時，只有 Core log 一行英文警告，Monitor UI 日誌區看不到；操作員不知道哪一台被淘汰。
- Monitor UI 日誌區目前只顯示 UI 自己的事件（Core 的 log 不會傳到 UI），所以現場盯 HMI 的人看不到任何連線耗時或失敗原因。

另外，指令路徑連線的 10 秒上限尚未決定，需要先在現場量到實際的握手耗時，本功能就是量測的工具。

## Solution

Core Service 為每一次「建立新 KVM 連線」、每一個設備指令、每一次 KVM 心跳記一筆**耗時紀錄**（毫秒），包含時間戳、設備 ID、IP、路徑、耗時、成功／失敗與失敗原因，指令另帶指令名稱，全部寫進 Core log 檔。Core 另把需要給人看的事件（連線嘗試、指令耗時、所有失敗、心跳狀態變化）放進 `core_status`，Monitor UI 輪詢時把新事件逐行加到日誌區：設備在前、毫秒在最後，失敗用日誌區既有的紅色錯誤樣式。心跳成功的逐次耗時只寫 Core log；同一台心跳連續失敗時，畫面只在狀態改變時各出一行（第一次失敗一行紅字、恢復一行一般顏色並附中斷秒數）。Log Solution 結果畫面完全不動。

## User Stories

連線耗時（Core log）

1. 身為現場工程師，我希望 Core 背景重連的每一次嘗試都在 Core log 留下耗時（ms），以便知道 JetKVM 握手實際要多久。
2. 身為現場工程師，我希望指令路徑（LabVIEW／Monitor UI 指令到達時）建立新連線也留下耗時，以便和背景重連比較。
3. 身為 RD，我希望紀錄註明是哪一條路徑（背景重連或指令路徑），以便分開統計。
4. 身為現場工程師，我希望連線失敗也記錄失敗前經過的毫秒數，以便知道是立即被拒還是等到逾時。
5. 身為現場工程師，我希望失敗紀錄寫出白話的失敗原因（逾時、連線被拒、登入失敗、裝置未回應、收不到畫面…），以便不用看 traceback 就知道該查網路、電源還是 HDMI。
6. 身為現場工程師，我希望背景重連超過 `connect_timeout_sec` 被放棄時記為「逾時」，耗時約等於上限，以便確認上限是否太短。
7. 身為 RD，我希望每筆紀錄都有設備 ID（例如 `DFU-03`）和 IP，以便不用自己對照設備清單。
8. 身為 RD，我希望重用既有連線不產生連線紀錄，以便紀錄只反映真正的握手（與 #134 計次範圍一致）。
9. 身為 RD，我希望背景重連排程到了但被指令搶先連上（沒有真的連線）時不產生紀錄，以便次數與實際嘗試一致。
10. 身為 RD，我希望 Core 關閉時被取消的連線也記一筆（原因為取消），以便知道那次嘗試沒有結果的原因。
11. 身為 RD，我希望耗時用單調時鐘計算，以便系統時間調整不會產生負數或跳動。

指令耗時（Core log）

12. 身為 RD，我希望每一個設備指令（例如 `check_start`、`input`、`snapshot`）從 Core 收到到送出回覆的毫秒數都記進 Core log，以便找出慢的指令。
13. 身為 RD，我希望指令紀錄帶指令名稱，以便依指令分類。
14. 身為 RD，我希望指令回覆不是 `ok` 時記為失敗並寫出回覆的 `error` 碼（例如 `timeout`、`device_busy`、`kvm_connect_failed`），以便和連線失敗對照。
15. 身為 RD，我希望 LabVIEW 中途斷線、沒有收到回覆的指令也記一筆，耗時算到 Core 判定沒有回覆為止，以便查得到「消失的指令」。
16. 身為 RD，我希望長輪詢逾時（`timeout_sec` 到）的指令記為失敗、原因逾時、耗時約等於 `timeout_sec`，以便和真正卡住區分。
17. 身為 RD，我希望指令耗時和該指令觸發的連線耗時是兩筆獨立紀錄，以便看出指令時間有多少花在連線上。
18. 身為 RD，我希望 `status`、`core_status`、`ping` 這類不綁設備的指令不記耗時，以便 Monitor UI 每 1.5 秒的輪詢不會灌爆 Core log。
19. 身為 LabVIEW 測試流程控制者，我希望記錄耗時不改變任何回覆內容與時機，以便現有流程完全不受影響。

心跳（Core log 與畫面）

20. 身為 RD，我希望每次 KVM 心跳的耗時與結果都寫進 Core log，以便事後查連線健康紀錄。
21. 身為操作員，我希望某台心跳判定不健康（偵測不到設備）時，Monitor UI 日誌區出現一行紅字，以便立刻知道哪一台出事。
22. 身為操作員，我希望同一台心跳持續失敗時畫面不重複刷同一句，只有第一次失敗一行紅字、恢復時一行一般顏色並寫出中斷了幾秒，以便日誌區不被洗版。
23. 身為 RD，我希望心跳持續失敗期間的每一次失敗仍寫進 Core log，以便完整重建時間線。
24. 身為操作員，我希望 12 台設備每 5 秒一次的心跳成功不出現在畫面上，以便日誌區只剩值得看的事件。

Monitor UI 日誌區

25. 身為操作員，我希望連線成功／失敗（兩條路徑）、LabVIEW 指令耗時、所有失敗都出現在 Monitor UI 日誌區，以便不用開 Core log 檔。
26. 身為操作員，我希望每行設備 ID 在前、毫秒在最後，例如 `07:45:12  DFU-03  連線成功（背景重連）  1240 ms`，以便一眼掃過。
27. 身為操作員，我希望失敗行用日誌區既有的紅色錯誤樣式，例如 `07:45:20  DFU-03  連線失敗（逾時）  10000 ms`，以便和成功行區分。
28. 身為操作員，我希望指令行寫出指令名稱，例如 `07:45:13  DFU-03  指令 check_start 回覆  85 ms`，以便知道是哪個指令慢。
29. 身為操作員，我希望行首時間是事件在 Core 發生的時間，而不是 UI 收到的時間，以便和 Core log 對得上。
30. 身為操作員，我希望切換中英文後新的行用目前語言顯示，以便和日誌區其他行一致。
31. 身為操作員，我希望 Monitor UI 剛開啟時不把 Core 之前累積的舊事件一次倒出來，以便畫面不爆量。
32. 身為操作員，我希望兩次輪詢之間事件太多、超出 Core 保留的筆數時，日誌區寫一行「略過 N 筆，詳見 Core log」，以便知道有漏看。
33. 身為操作員，我希望 Core 重啟後新的事件照常出現、不會因編號重來而漏掉，以便重啟後仍可觀察。
34. 身為 RD，我希望這些行也照常寫進 Monitor UI 自己的每日 log 檔（雙語格式），以便現場回報時附檔即可。
35. 身為 RD，我希望 Log Solution 結果畫面、狀態標記、定位點、結果色帶、站別列與警示橫幅完全不因本功能改變，以便不影響顯示契約與既有判讀。

## Implementation Decisions

- **耗時紀錄（timing record）**：Core 內一個小型值物件，欄位：
  - `ts`：事件結束時的本機時間（epoch 秒，含毫秒）
  - `device`：設備 ID；IP 不在設備清單時為空字串
  - `ip`：正規化後的 KVM IP
  - `path`：`background_reconnect`（Core 背景重連）／`command_connect`（指令路徑建立連線）／`command`（設備指令）／`heartbeat`（KVM 心跳）
  - `command`：指令名稱，只有 `path = command` 時有值
  - `elapsed_ms`：整數毫秒，單調時鐘計算，可注入時鐘供測試
  - `ok`：成功／失敗
  - `reason`：失敗原因碼，成功為空字串
- **計時起訖**（使用者 2026-10-28 決定）：
  - 連線：從開始建立新連線（建立 JetKVM client 並呼叫 `connect()`）起，到 `connect()` 正常返回（rpc 通道開啟且收到第一張影格，即連線池放入新連線的那一刻）或判定失敗（例外、逾時、取消）為止。重用既有連線、背景重連到期前發現已連上而跳過，都不算一次連線、不產生紀錄（與 #134 計次一致）。
  - 指令：從 Core 收到該行指令起，到 Core 送出回覆為止；沒有回覆（LabVIEW 斷線，Core 不寫回覆）時算到 Core 判定沒有回覆為止。只記通過驗證、綁定設備（`device_bound`）的指令；`status`／`core_status`／`ping`／`shutdown` 及驗證失敗的請求不記。被 Device Lock 拒絕（`device_busy`）的指令照記，原因 `device_busy`。
  - 心跳：每次心跳對每條池中連線的健康檢查記一筆；結果為不健康時記失敗。
  - 單位毫秒。
- **失敗原因碼**（白話對照由 Monitor UI 字串表提供）：
  - 連線：`timeout`（`asyncio.TimeoutError`，含背景重連超過 `connect_timeout_sec`）、`connection_refused`（`ConnectionRefusedError`）、`unreachable`（其他 `OSError`／網路錯誤）、`login_failed`、`rpc_not_open`（裝置未回應）、`no_signal`（收不到畫面）、`cancelled`（Core 關閉或工作取消）、`error`（其他，Core log 另附例外類別）。JetKVM client 目前以 `RuntimeError` 中文訊息區分登入失敗／rpc 未開／無畫面；實作時可由 client 改拋可辨識的例外或帶原因碼，但不改 client 對連線池的介面契約（`connect`／`close`／`is_healthy`／`latest_frame`）。
  - 指令：回覆的 `error` 碼原樣（`timeout`、`device_busy`、`kvm_connect_failed`、`frame_frozen`…）；沒有回覆為 `client_disconnected`；處理中未預期例外為 `internal_error`。
  - 心跳：不健康原因 `track_ended`（影像軌道結束）、`peer_failed`、`peer_closed`、`not_connected`。
- **設備 ID 對照**：KVM Connection Pool 只認 IP；設備清單（DeviceRegistry）保證每台設備的 `kvm_ip` 唯一（IPv4、驗證時擋重複），因此 IP → 設備 ID 一對一。連線池不認識設備清單：連線池在每次連線／心跳結束時把「IP＋路徑＋耗時＋結果＋原因」交給一個可注入的耗時觀察者（建構子參數，預設不做事），由擁有設備清單的 TCP JSON server 補上設備 ID，再寫 Core log 與事件緩衝。指令紀錄在 TCP JSON server 的指令派送處產生，設備 ID 直接取自已驗證的請求。
- **Core log 格式**：沿用既有雙語行（`HH:MM:SS | EN | 中文`，`BilingualFormatter`，每日一檔），logger 名稱固定（例如 `KVMTiming`），層級：成功 INFO、失敗 WARNING。訊息為固定 key=value 樣式，方便 grep：
  - `KVM timing device=DFU-03 ip=192.168.1.10 path=background_reconnect result=ok elapsed_ms=1240 | KVM 計時 設備=DFU-03 IP=192.168.1.10 路徑=背景重連 結果=成功 耗時=1240 ms`
  - 失敗加 `reason=timeout`／`原因=逾時`；指令加 `command=check_start`／`指令=check_start`。
  - 心跳成功也寫（INFO）；12 台 × 每 5 秒的量只進 Core log 檔。
- **心跳畫面規則**（使用者 2026-10-28 決定，原列於未釐清問題，已移除）：
  - 心跳耗時（含成功）只寫 Core log 檔。
  - 心跳失敗／偵測不到設備**必須**也出現在 Monitor UI 日誌區。
  - 每台設備只在**狀態改變**時上畫面：第一次失敗一行紅字，例如 `07:50:05  DFU-03  心跳偵測不到  逾時 5000 ms`；恢復時一行一般顏色並附中斷秒數，例如 `07:51:20  DFU-03  心跳恢復  已中斷 75 秒`。兩者之間的重複失敗只寫 Core log 檔。
  - 狀態追蹤在 Core（每設備一份，記憶體內，Core 重啟清除）；中斷秒數 = 恢復時間 − 第一次失敗時間，四捨五入到整秒。
  - 「恢復」= 該設備第一次失敗之後，再次有一次健康的心跳（通常要等該設備重新建立連線、進入連線池之後的下一次心跳）。
- **畫面顯示範圍**（使用者 2026-10-28 決定）：連線嘗試（兩條路徑，成功與失敗）、LabVIEW 指令耗時、所有失敗（含心跳失敗）；心跳成功不上畫面；心跳狀態變化依上條規則。
- **Core → Monitor UI 傳遞**：Monitor UI 目前只透過每 1.5 秒輪詢 `core_status` 取得 Core 狀態，Core log 本身不會到 UI。新增 `core_status.timing_events`（僅 Monitor UI 使用，非 LabVIEW 協定，LabVIEW 的 `status` 不變）：
  - Core 保留最近 N 筆（例如 200）需上畫面的事件，每筆帶遞增 `seq` 與上述紀錄欄位；心跳狀態變化事件另帶 `kind`（`heartbeat_lost`／`heartbeat_recovered`）與 `outage_sec`（恢復時）。
  - 回覆另帶 `timing_seq`（目前最大序號）與 Core 啟動識別（例如沿用 `uptime_seconds` 判斷重啟，或另給 `core_instance`），讓 UI 偵測 Core 重啟。
  - Monitor UI 記住看過的最大 `seq`，只附加更新的事件；第一次成功輪詢只記基準、不顯示舊事件；偵測到 Core 重啟則從新 Core 的事件重新開始；序號有缺口（超出保留筆數）時寫一行「略過 N 筆，詳見 Core log」。
  - `docs/PROTOCOL.md` §5.9 新增欄位說明。
- **Monitor UI 日誌區**：
  - 行格式依設計師建議：`HH:MM:SS  <設備 ID>  <事件>  <耗時> ms`，時間取事件的 `ts`（Core 時間），一事件一行。
  - 失敗行與心跳失敗行套用日誌區既有 `error` 樣式（紅字，`LOG_ERROR_FG`）；其他用一般顏色。
  - 事件文字經 `ui_i18n` 字串表（`en`／`zh` 同 key，#106 一致性測試照常）；中文依設計師：連線成功（背景重連）／連線成功（指令）／連線失敗（<原因>）／指令 <名稱> 回覆／指令 <名稱> 失敗（<原因>）／心跳偵測不到／心跳恢復。英文字串為草案，可由設計師調整。
  - 同時照常寫入 Monitor UI 每日 log 檔（`DailyLogFile`，雙語）。
  - 不加篩選或上色開關（v1 不做）。
- **不改的部分**：連線失敗計次（3／60／10／10）、背景重連排程、指令回覆內容與 envelope、Log Solution 結果畫面與顯示契約（狀態標記、定位點、排數格、結果色帶）、站別列與警示橫幅。

## Testing Decisions

- 好的測試只驗證外部可觀察行為：Core log 寫出的行（以 `BilingualFormatter` 格式化後的文字）、`core_status` 回覆的 JSON、Monitor UI 日誌區的文字與顏色 tag；不斷言私有欄位或呼叫次數。
- **切入點**：
  1. KVM Connection Pool＋假 KVM session（`tests/fake_kvm.py` 的 `FakeKVMSession`，可控制 `connect()` 成功、拋錯、卡住）＋可注入時鐘：驗證連線與心跳紀錄（路徑、耗時、原因）。
  2. TCP JSON 協定（真實 socket，`protocol_helpers.py`）：驗證指令紀錄、設備 ID 對照、`core_status.timing_events`。
  3. Monitor UI Tk 測試：餵假的 `core_status` 回覆給日誌區，驗證行文字、順序、紅色 tag、略過提示、Core 重啟。
- **Prior art**：`tests/test_kvm_pool_connect_failures_134.py`（假 session、可注入時鐘、背景重連）、`tests/test_connect_failures_134.py`、`tests/test_core_status_134.py`（`core_status` 欄位）、`tests/test_bilingual_log.py`（雙語行）、`tests/test_monitor_hmi_134.py`／`test_monitor_hmi_105.py`（Tk 日誌區與 log 檔）、`tests/test_long_polling.py`（長輪詢逾時、斷線）。
- 全套測試以本機權限執行（需要 bind localhost），完整套件不比基準多出失敗。

## Out of Scope

- 指令路徑連線的 10 秒上限（另開票，待現場量到實際握手耗時後再決定）。
- #149、#150、#151、#152。
- PR #153 第二輪審查的 3 項非阻擋建議。
- Log Solution 結果畫面（狀態標記、定位點、排數格、結果色帶）及任何顯示契約變更。
- 日誌區的篩選或上色開關（v1 不做）。
- 改變心跳的判斷方式（例如改成實際網路往返）或心跳淘汰後自動背景重連。
- LabVIEW 端的計時或 LabVIEW 協定（`status`）新增欄位。

## 未釐清問題

1. **心跳失敗行的毫秒數代表什麼**（待使用者確認，不在本 spec 決定）：現行 KVM 心跳不送網路封包，只在本機讀 JetKVM client 的 `is_healthy()`（影像軌道結束或 peer connection failed／closed 即不健康），沒有「逾時」，單次耗時接近 0 ms。設計範例 `心跳偵測不到  逾時 5000 ms` 的 5000 ms 目前沒有對應的量測值。提案（未決定）：失敗行寫「距上次正常心跳」的毫秒數（最多約 `heartbeat_interval` 5000 ms），括號寫實際原因（影像中斷／連線中斷）而非「逾時」。
2. **Monitor UI 自己送的設備指令要不要上畫面**（待確認）：Monitor UI 的即時畫面每秒多次送 `snapshot`，手動檢查送 `inspect`，都走同一條指令派送，會洗版。Core 無正式來源欄位，但 Monitor UI 的 `req_id` 一律以 `ui-` 開頭。提案（未決定）：`req_id` 以 `ui-` 開頭的指令只寫 Core log、不上畫面（失敗仍上畫面？亦待確認）。
3. **「指令耗時」的量測端**（確認解讀）：本 spec 依需求第 3 點解讀為 Core 端「收到指令 → 送出回覆」（與回覆 envelope 的 `elapsed_sec` 同一段）。若使用者要的是 LabVIEW 端往返（含網路），需由 LabVIEW 實作，不在本 spec。
4. **畫面篩選／上色**：是否在日誌區加篩選或依類型上色。建議 v1 不做（已列入 Out of Scope，留待使用者確認後另開票）。
5. **指令路徑 10 秒上限**：不在本 spec；等現場用本功能量到握手耗時後另開票決定。

## Further Notes

- 心跳淘汰（不健康）後，#134 的背景重連不會自動排程（背景重連只在「建立新連線失敗」後才排）；因此「心跳恢復」通常要等該設備下一個指令重新建立連線之後才會出現，期間可能很久。這是現行行為，本 spec 不改。
- 心跳淘汰時若連線正被指令使用，淘汰會延後到釋放；期間每次心跳都會失敗，正好由「只在狀態改變時上畫面」規則吸收。
- 背景重連與連線失敗異常的計次邏輯（`ConnectFailures`）不變；耗時紀錄是旁路觀察，不影響計次。
- Monitor UI 日誌區高度約 6～8 行；畫面範圍的決定就是為了 12 台設備同時運作時不洗版。
