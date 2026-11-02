# fake core：沒有 KVM、BT 實機也能測 Core Client VI

模擬 Core，走真的協定（`docs/PROTOCOL.md`）：TCP、一行一個 JSON、LF 結尾、`protocol_version` 為 `"1.0"`。
envelope 欄位、`data` 預設形狀、錯誤碼與 message 照 `host-app/tcp_server.py`。只用 Python 標準函式庫，單一程式。

## 啟動

```
python tools/fake_core/fake_core.py
```

預設聽 `127.0.0.1:5000`，只接受本機連線；啟動後印出 `listening on HOST:PORT`。
跨機器測試時，使用 `--host 0.0.0.0` 監聽所有網卡，或使用 `--host <網卡位址>` 指定網卡；監聽 `0.0.0.0` 時也會嘗試印出區網位址。
視窗會留著，直接在視窗輸入指令觸發故障（見下）。沒有終端機（stdin 結束）時伺服器仍繼續跑，Ctrl-C 或 `quit` 結束。

## 參數

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 監聽位址；跨機器測試可指定 `0.0.0.0` 或網卡位址 |
| `--port` | `5000` | TCP port；`0` 由系統指派（實際 port 看 `listening on` 那行） |
| `--device` | `BT-01` | 設備 ID |
| `--station` | `BT` | 站別 `BT`／`DFU`／`FCT`（FCT 沒有治具訊號，`monitor_start` 成功即視為治具已推入） |
| `--channels` | `4` | 通道數 |
| `--finish-delay` | `5` | `start_test` 後幾秒出結果畫面 |
| `--monitor-delay` | `0.5` | `monitor_start` 耗時秒數 |
| `--result` | `PASS` | `PASS`／`FAIL`／`TIMEOUT`，或逐通道清單 `PASS,FAIL,PASS,NOTEST` |
| `--not-found-delay` | `30` | `log_solution_not_found` 故障下 `check_finish` 長輪詢等幾秒才回錯誤（對應真 Core 的 `VISIBILITY_LOST_SEC`） |
| `--poll` | `0.5` | `check_start`／`check_finish` 內部輪詢間隔（對應真 Core 的 `CHECK_POLL_INTERVAL_SEC`） |
| `--scenario` | 無（正常） | 情境名稱或別名，見下方「情境」 |

## 支援的指令

`ping`、`status`、`monitor_start`、`start_test`、`check_start`、`check_finish`。驗證順序與錯誤碼同真 Core
（`invalid_json`、`invalid_request`、`unsupported_protocol_version`、`missing_req_id`、`missing_command`、
`unknown_command`、`unknown_field`、`forbidden_field`、`missing_device`、`unknown_device`、`invalid_argument`；
FCT 的 `start_test` 仍回 `unsupported_command`，此為協定行為）。FCT 沒有治具訊號，`monitor_start` 成功即視為治具已推入
（之後照 finish-delay 出結果，可跑完一輪）。站別執行中不能切換，互動輸入 `station X` 只會回說明。

`snapshot`、`inspect`、`input`、`core_status`、`shutdown` 不模擬，通過驗證後回
`error=unsupported_command`（message 說明 fake core 不模擬），不會假裝成功。

### 正常流程

1. `monitor_start`（`timeout_sec` 1–300）→ ok，`data = {state: "monitoring", capacity: 通道數}`
2. `start_test` → ok，`data.action` 為 `start_all`（BT）或 `ok`（DFU）
3. `check_start`（可省略、可重複；`timeout_sec` 0–3600）→ 測試進行中且連續兩次讀取相隔 ≤ 1 秒：
   `kind=started`、`state=monitoring`、`reason=activity_confirmed`、`active` 為每個通道 `{slot, evidence:"testing"}`、`results=[]`。
   `timeout_sec=0` 第 1 次回 `waiting`／`monitoring`，1 秒內第 2 次回 `started`；長輪詢約 `--poll` 秒後回 `started`；
   兩次相隔 > 1 秒不會配對。`started` 不消耗結果，之後 `check_finish` 仍可 `taken`。結果畫面已出現時 `check_start` 也會 `taken`
   （之後 `check_finish`／`check_start` 都回 `already_taken`）。逾時回 `status=timeout`、`error=timeout`，`data` 為最後讀值。
4. `check_finish`（`timeout_sec` 0–3600）→ 在 `--finish-delay` 之後回 `kind=taken`、`reason=reliable_complete`、`results` 逐通道、`active=[]`
   （第一次看到完成畫面先回 `waiting`／`confirming_complete_frame`，下一次輪詢約 `--poll` 秒後 `taken`，所以長輪詢會自然取到；`check_finish` 不會回 `started`）
5. 再查一次 → `kind=already_taken`，同一組 `results`

沒有 `monitor_start` 就 `check_start`／`check_finish`、或非監控中 `start_test` → `monitor_not_ready`。
同設備上鎖指令（`monitor_start`／`start_test`／`check_start`／`check_finish`）並行時第二筆回 `device_busy`。
`check_start` 與 `check_finish` 共用判讀順序與故障行為（待確認、`shutdown`、`heartbeat`、`not_found`、`timeout`、client 斷線不回覆）。

## 情境（scenario）

情境改變「下一次 `start_test` 起」發生的事，持續到 `clear`／`reset` 或改選別的情境。三種選法：
啟動參數 `--scenario NAME`、在視窗直接輸入名稱（或別名）、在視窗輸入 `scenario NAME`。`state` 顯示目前情境，`help` 列出全部。

| 情境（別名） | 發生什麼事 | 預期回覆 |
| --- | --- | --- |
| `cs_started` (`css`) | 正常；選它等於清除情境 | `check_start` → ok `started`；之後 `check_finish` → `taken` |
| `cs_taken` (`cstk`) | `start_test` ok，結果畫面立刻出現（看不到測試過程） | `check_start` 長輪詢 → ok `taken`（`state=complete`、`reason=reliable_complete`、逐通道 `results`）；`timeout_sec=0` 第 1 次 `waiting`／`complete`／`confirming_complete_frame`，1 秒內第 2 次 `taken`。之後 `check_finish`（任何 `timeout_sec`）與再次 `check_start` → ok `already_taken`、同一組 `results` |
| `cs_timeout` (`csto`) | `start_test` ok，但看不到測試開始，也永遠沒有結果畫面 | `check_start` `timeout_sec>0` → `status=timeout`、`error=timeout`、`data`：`kind=waiting`、`state=monitoring`、`reason=monitoring`、`active=[]`、`results=[]`；`timeout_sec=0` → ok `waiting`。`check_finish` 同樣 |
| `cs_paused` (`csp`) | `start_test` ok，看不到測試；`--finish-delay` 秒後畫面進入待確認（通道沒變黃 → NOTEST → 待確認） | 延遲前 `timeout_sec=0` 的 `check_start` → ok `waiting`／`monitoring`；`check_start` 長輪詢或延遲後 → ok `paused`（`state=review`、`reason=confirmation_required`、`results=[]`）。之後 `confirm`／`confirm standby` 照常 |
| `start_test_drop_not_started` (`stdrop`) | `start_test` 時 server 不回覆就關掉該連線，測試沒有開始 | `start_test` 收不到回覆（連線被關）。重連後 `check_start` 永遠不會 `started`，逾時回 `timeout`（資料同 `cs_timeout`）；本輪開始請求仍在，所以 `check_finish` `timeout_sec=0` 回 ok `waiting`，不是 `monitor_not_ready` |
| `start_test_drop` (`stds`) | `start_test` 時 server 不回覆就關掉該連線，但測試在內部照常開始（等同 `cs_started`） | `start_test` 收不到回覆（連線被關）。重連後 `check_start` → ok `started`（`reason=activity_confirmed`、`active` 為全部通道 `testing`、`results=[]`）；`--finish-delay` 秒後 `check_finish` → `taken`（設定的 `results`），再查 → `already_taken` |

選錯名稱會回「未知情境」並列出可用名稱。

## 故障：視窗輸入指令（括號內是縮寫）

| 指令 | 型態 | 預期回覆 |
| --- | --- | --- |
| `disconnect` (`dc`) | 一次 | 立即關閉所有 client socket；進行中的請求不回覆；伺服器繼續聽 |
| `timeout` (`to`) | 持續 | `check_finish` 不交付，等滿 `timeout_sec` 回 `status=timeout`、`error=timeout`，`data` 為最後觀察值（`waiting`／`monitoring`）；`timeout_sec=0` 回 ok `waiting` |
| `heartbeat` (`hb`) | 持續 | `status` 該設備 `frozen=true`、`connected=false`；設備指令回 `kvm_connect_failed`（連線中斷） |
| `connect_failed` (`cf`) | 持續 | `status` `connect_failed=true`、`connect_fail_count=3`、`connected=false`；設備指令回 `kvm_connect_failed` |
| `review` (`rv`) | 轉換 | 畫面進入待確認：`check_finish`（含長輪詢）立即回 ok、`kind=paused`、`state=review`、`reason=confirmation_required`、`results=[]` |
| `confirm` (`cn`)／`confirm complete` | 轉換 | 待確認 → 完成。`timeout_sec=0`：第 1 次 `waiting`／`confirming_complete_frame`，1 秒內第 2 次 `taken`；長輪詢直接 `taken`；兩次間隔 > 1 秒則一直不 `taken` |
| `confirm standby` | 轉換 | 待確認 → 待機。第 1 次 `error round_lost`（`data.kind=unknown`、`state=""`），第 2 次 ok `waiting`／`standby` |
| `shutdown` (`sd`) | 持續 | 進行中的 `check_finish` 與之後所有請求回 `server_shutting_down`；輪次重設為待機 |
| `not_found` (`nf`) | 持續 | `monitor_start` 回 `log_solution_not_found`（`data` 為 `state=""`、`capacity=0`）；`check_finish` `timeout_sec=0` 回 ok、`kind=unknown`、`reason=locators_missing_or_ambiguous`；長輪詢等 `--not-found-delay` 秒後回 `log_solution_not_found`（`timeout_sec` 較小則先回 `timeout`）；`start_test` 回 `monitor_not_ready`（unreadable） |
| `clear` (`c`) | | 解除所有持續故障與情境 |
| `reset` (`r`) | | 解除故障與情境，並把輪次重設為待機 |
| `scenario NAME` (`sc`) | | 選情境，見上方「情境」 |
| `result X` (`res`) | | 執行中改結果，例如 `result FAIL`、`result PASS,FAIL,PASS,NOTEST` |
| `state` (`s`) | | 顯示目前畫面、旗標與故障 |
| `help` (`h`)／`quit` (`q`) | | 說明／結束 |

待確認中送 `monitor_start` 回 `log_solution_review`；之後 `confirm` 完成，`check_finish` 回
`manual_verification_required`（本輪開始請求被清除，再查是 `monitor_not_ready`）。
`review` 在測試進行中或結果已出現時都可以用。

「待確認／確認」的行為依 2026-10-28 用 main `96e302e` 真 `TcpJsonServer` 的 C′ probe 實測結果。

## LabVIEW 測試步驟範例

先讓 VI 走到 `monitor_start` → `start_test` → `check_finish` 長輪詢中（或指定的步驟），再在 fake core 視窗輸入：

| 要測的情境 | 視窗輸入 | VI 應該看到 |
| --- | --- | --- |
| 斷線 | `disconnect` | TCP 連線被關閉，進行中的 `check_finish` 沒有回覆（讀取得到連線關閉／逾時），VI 需重連 |
| 逾時 | `timeout` | `check_finish` 等滿 `timeout_sec` 回 `status=timeout`、`error=timeout`，`data.state=monitoring` |
| 心跳停止 | `heartbeat` | `status` 的設備 `frozen=true`、`connected=false`；設備指令回 `kvm_connect_failed` |
| connect_failed | `connect_failed` | `status` 的 `connect_failed=true`、`connect_fail_count=3`；設備指令回 `kvm_connect_failed` |
| 待確認 | `review` | `check_finish` 立即回 ok、`kind=paused`、`reason=confirmation_required` |
| 確認後完成 | `review` 再 `confirm` | `waiting`／`confirming_complete_frame` 後 `taken`（`timeout_sec=0` 需 1 秒內連查兩次；長輪詢直接 `taken`） |
| 確認後待機 | `review` 再 `confirm standby` | 第 1 次 `error round_lost`，之後 ok `waiting`／`standby` |
| 待確認中開始監控 | `review`，VI 送 `monitor_start`，再 `confirm` | `monitor_start` 回 `log_solution_review`；`check_finish` 回 `manual_verification_required`，再查回 `monitor_not_ready` |
| 伺服器關閉 | `shutdown` | 進行中與之後的請求都回 `server_shutting_down` |
| 找不到 Log Solution | `not_found` | `monitor_start` 回 `log_solution_not_found`；`check_finish` `timeout_sec=0` 回 ok、`kind=unknown`、`reason=locators_missing_or_ambiguous`，長輪詢等 `--not-found-delay` 秒後回 `log_solution_not_found` |
| `check_start` 看到已開始 | （不用輸入，預設 `--finish-delay` 夠長即可） | 連查兩次（相隔 ≤ 1 秒）或長輪詢 → `kind=started`、`active` 為全部通道；`check_finish` 之後仍 `taken` |
| `check_start` 直接交付 | `cstk`（再跑 `monitor_start` → `start_test`） | `check_start` → `taken`；`check_finish` 與再次 `check_start` → `already_taken` |
| `check_start` 逾時 | `csto`（再跑 `monitor_start` → `start_test`） | `check_start` → `status=timeout`、`error=timeout`、`data` 為 `waiting`／`monitoring`；`timeout_sec=0` → ok `waiting` |
| `check_start` 待確認 | `csp`（再跑 `monitor_start` → `start_test`，等 `--finish-delay` 秒） | `check_start` → ok `paused`／`review`／`confirmation_required` |
| `start_test` 沒有回覆 | `stdrop`（再跑 `monitor_start` → `start_test`） | `start_test` 連線被關、沒有回覆；重連後 `check_start` → `timeout`；`check_finish` `timeout_sec=0` → ok `waiting` |
| `start_test` 沒有回覆但測試已開始 | `stds`（再跑 `monitor_start` → `start_test`） | `start_test` 連線被關、沒有回覆；重連後 `check_start` → `started`；`check_finish` → `taken`，再查 → `already_taken` |
| 測完還原 | `clear`（解除故障與情境）或 `reset`（連輪次一起重設） | 回到正常流程 |

## 之後接「對方模擬器」的擴充點

目前不做手臂觸發、PLC IO 交握、Busy／Ready、結果寫入 PLC 暫存器。結構上預留兩處：

- **畫面／輪次轉換是公開方法**：`FakeCore.devices["BT-01"]` 是 `Device`，有 `enter_review()`、`confirm(to)`、
  `monitor_ready()`、`test_started()`、`show_result()`、`observe_activity()`、`reset()`，以及 `screen`、`start_requested`、`armed`、`delivered` 屬性；
  `core.results` 是目前設定的逐通道結果；`core.command(text)`（與視窗輸入同一個入口）也可直接當程式介面用。
- **事件掛鉤**：`core.add_listener(callback)`，`callback(event_name, device_id, details)`。事件：
  `monitor_start`、`start_test`、`result_screen`、`taken`、`review`、`confirm`、`fault`、`scenario`。

```python
# 在 tools/fake_core 目錄下執行，或先 sys.path.insert(0, "tools/fake_core")
from fake_core import FakeCore

core = FakeCore(port=0)
core.start()
core.add_listener(lambda name, dev, details: print(name, dev, details))
```

## 測試

```
python -m pytest tools/fake_core -q
```

測試用真 TCP socket 對 `--port 0` 啟動的 fake core 驗證，延遲全部可設定、用小值，整組約十幾秒。
