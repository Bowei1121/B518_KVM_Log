# 部署說明（B518 九台設備）

對應 spec #1 的 user story 35–40、54 與部署實作決策。本文件只寫上位機的部署與維護步驟；LabVIEW 端的改版見 `docs/LABVIEW_LOG_SOLUTION_INTEGRATION.md`。

## 1. 組成

- **上位機（一台 Windows PC）**：Core Service 與 Monitor UI 同機執行。Core 只開一個 TCP 埠（預設 5000），每台設備由 LabVIEW 開一條獨立連線到同一埠（ADR-0006）。
- **九台 JetKVM**：DFU-01～DFU-07、FCT-01、BT-01，各接一台測試機與一台測試機電腦（ADR-0007）。
- **交換機**：EKI-2720G，非網管交換機。不做 VLAN 或 port 設定，所有設備與上位機以靜態 IP 互連。

## 2. 靜態 IP 規劃

下表與設備清單範例（`config/devices.example.json`）及 Monitor UI 設定頁的預填值一致。

| 設備 | IP | 站別 | 通道數 |
| --- | --- | --- | --- |
| 上位機 | 192.168.132.10 | — | — |
| DFU-01～DFU-07 | 192.168.132.70～192.168.132.76 | DFU | 4 |
| FCT-01 | 192.168.132.77 | FCT | 4 |
| BT-01 | 192.168.132.78 | BT | 4 |

- 子網路遮罩 255.255.255.0。這些位址必須在路由器或 DHCP 範圍之外，避免衝突。
- 每台 JetKVM 與上位機都要手動設定，交換機不提供 DHCP。
- 同一網段內若有其他設備使用相同位址，不會被設備清單發現（清單只檢查檔內重複），需由現場確認。

**未驗證項目**：JetKVM 正式版是否提供靜態 IP 設定選項。社群資料指 0.5.0 dev 起支援，正式版待現場確認。在確認前，部署人員須依 JetKVM 管理介面的實際選項設定，並把結果記錄在部署紀錄中。

## 3. 設備清單維護

設備清單位於上位機 `~/Documents/template/devices.json`，是唯一的設備設定來源。

- 首次部署：Monitor UI 的「⚙ 設備設定」頁會在沒有清單時預填 B518 九台（通道數 4、規劃 IP）。核對 IP 後按「儲存」。
- 修改：在設定頁編輯，或直接編輯檔案。設定頁儲存前會驗證並逐列顯示錯誤；儲存時會把舊檔備份為 `devices-backup-<時間>.json`。
- 連線確認：設定頁的「測試」與「全部測試」以 TCP 連線到各 KVM 的網頁埠（80）並回報延遲。這是可達性檢查，不是 KVM 功能檢查。
- 生效：清單只在 Core 啟動時讀取。儲存後必須重啟 Core。設定頁的「重啟 Core」在有未儲存修改，或任一設備正在執行動作時會拒絕，並列出忙碌的設備。

清單格式：

```json
{
  "DFU-01": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4}
}
```

- 設備 ID 格式為 `<站別>-<兩位數>`，前綴必須等於站別。
- 測試通道數為正整數，須與該設備 Log Solution 的 Slot 數一致。

## 4. ATE 與 Log Solution 現場設定

橋接以 ATE 畫面與 Log Solution 畫面的位置判讀與操作，以下設定須在每台 ATE 電腦完成。

- **ATE 視窗**：DFU、FCT、BT 的 ATE 測試程式視窗不可最大化。Log Solution 視窗貼齊畫面右側邊緣，因此**右側不可被遮住**；左側允許被 ATE 視窗遮住。
- **被遮住時的恢復**：Log Solution 整個或部分被遮住（例如左側被 ATE 視窗遮住）、已關閉或縮到 Dock，連續 30 秒看不到時，`check` 會點 Dock 圖示把它拉回最上層，5 秒後重新判讀；同一 `check` 最多 3 次，仍看不到回 `log_solution_not_visible`。Dock 上找不到 Log Solution 圖示時回 `log_solution_not_found`，屬嚴重異常，需停線檢查（請確認 Log Solution 圖示固定在 Dock）。
- **Log Solution 保持開啟**：若已關閉，`monitor_start` 的 Dock 點擊會讓 macOS 重新開啟，開啟後為 Standby，流程可繼續。
- **通道數一致**：每站 Log Solution profile 的通道數（Slot 數）必須等於 `devices.json` 的 `channels`。不一致時 `check` 回 `capacity_mismatch`，不交付結果。
- **共用 Dock 模板**：在「創建 Pattern」建立 COMMON 的 `log_solution_dock_icon`，檔案為 `~/Documents/template/COMMON/COMMON_log_solution_dock_icon.png`。三站共用，缺檔時 `monitor_start` 回 `hid_failed`。
- **FCT 推入前發訊號**：治具推入不由 LabVIEW 控制。設備須在推入前發訊號給 LabVIEW，LabVIEW 先呼叫 `monitor_start`，再呼叫 `check`。Log Solution FCT profile 的 start 期限需涵蓋「訊號到首次寫檔」的時間，建議先設 120 秒。
- **Log Solution 1.2**：本橋接不依賴 1.2。1.2 的修改建議（人工停止顯示 Complete、視窗前景時機）見 [LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md](LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md)，由 Log Solution 開發者自行開票。1.1 下人工停止的輪次會回 `round_lost`，LabVIEW 應暫停並人工處理，不得判 FAIL。

## 5. 啟動與重啟

Core Service 在上位機上以前景或背景方式執行：

```sh
python host-app/core_daemon.py run --port 5000
python host-app/core_daemon.py status --port 5000
python host-app/core_daemon.py restart --port 5000
```

- 清單位置預設為 `~/Documents/template/devices.json`。可用 `--devices-file` 指定（僅 `run` 支援），或設定環境變數 `B518_DEVICES_FILE`。
- `start` 與 `restart` 啟動的子行程不會傳遞 `--devices-file`，只會使用環境變數或預設位置。若要指定其他清單位置，請改用 `run` 或設定環境變數。
- Core 缺檔或清單有誤時拒絕啟動，錯誤會寫入 log 與終端機。

## 6. 上線前檢查清單

1. 設備清單儲存成功，設定頁沒有錯誤列。
2. Core 啟動成功（`status` 回報 running）。
3. Monitor UI 設備下拉選單列出九台。
4. 設定頁「全部測試」九台皆成功（連線到各 KVM 網頁埠）。
5. 逐台以 Monitor UI 的快照與手動檢查確認畫面可讀。
6. 依 `docs/LOAD_TEST.md` 執行 30 分鐘錄影重播壓測，報告全部通過。
7. LabVIEW 端已依 `docs/PROTOCOL.md` 改版（spec #13，人工）。
8. 每台 ATE 視窗未最大化，且未遮住畫面右側（見第 4 節）。
9. `~/Documents/template/COMMON/COMMON_log_solution_dock_icon.png` 存在。
10. 每站 Log Solution profile 通道數與 `devices.json` 的 `channels` 一致。
11. 逐站依 `docs/PROTOCOL.md` 第 4.1 節順序實測：`monitor_start` 成功、`start_test` 點擊正確、`check` 取得 `taken`（或 `paused`）。

## 7. 防火牆與連線

- 上位機需允許入站 TCP 5000（或所用埠）給 LabVIEW 所在的電腦。
- 上位機需能對九台 KVM 的 80 埠發起 TCP 連線（設定頁測試與 Core 連線都需要）。

## 8. 未驗證與待現場確認

- JetKVM 靜態 IP 設定選項（見第 2 節）。
- FCT 推入前訊號到首次寫檔的實際時間，決定 Log Solution FCT start 期限（見第 4 節）。
- 人員按停止監控後，Log Solution header 的實際標記是否符合規定：1.2 為 Complete（含 `STOPPED`，`check` 正常交付 `taken`）；1.1 為 Standby（`check` 回 `round_lost`）。見 `docs/PROTOCOL.md` 5.4 與第 10 節。
- 九台同時運作的 CPU、記憶體與回覆延遲，需以混合真機的錄影重播壓測實測（見 `docs/LOAD_TEST.md`）。
- LabVIEW 端 type definition 與 VI 改版（spec #13，人工）。
