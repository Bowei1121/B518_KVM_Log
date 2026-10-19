# 現場驗證清單

開發階段無法連實機驗證的項目，集中記錄在這裡；到現場後逐項執行，填入日期與結果。未驗證項目不得視為已完成。

欄位：**步驟**／**預期結果**／**驗證日期**／**結果（通過／失敗＋備註）**。

## 部署與設備（原 `DEPLOYMENT.md` 第 8 節、`LABVIEW_LOG_SOLUTION_INTEGRATION.md`）

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| D1 | 檢查 JetKVM 韌體的靜態 IP 設定選項（`DEPLOYMENT.md` 第 2 節） | 九台都能設定靜態 IP 並在重開機後保留 | | |
| D2 | 量測 FCT 推入前訊號到首次寫檔的時間（`DEPLOYMENT.md` 第 4 節） | 取得實測值，據此決定 Log Solution FCT start 期限 | | |
| D3 | 人員按停止監控，觀察 Log Solution header 標記（`PROTOCOL.md` 5.4、第 10 節） | 1.2：Complete 含 `STOPPED`，`check_finish` 交付 `taken`；1.1：Standby，回 `round_lost` | | |
| D4 | 混合真機的錄影重播壓測，九台同時運作（`LOAD_TEST.md`） | CPU、記憶體、回覆延遲在 `DEFAULT_LIMITS` 內 | | |
| D5 | LabVIEW 端 type definition 與 VI 改版後實際收發（#13，人工） | 依 `PROTOCOL.md` 與 `LABVIEW_HANDOFF.md` 的呼叫順序全部指令往返正常 | | |
| D6 | 實際 JetKVM 的壓縮與縮放下讀取 Log Solution 畫面 | 狀態標記、20 格結果與已開始通道判讀正確 | | |
| D7 | ATE 動作（SN 輸入、開始測試、Dock 點擊）實機操作 | 點擊位置正確、HID 無錯誤 | | |

## C3_2：KVM 斷線偵測（#58）

前提：#58 已部署；一台設備已用 Monitor UI 確認可快照。

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| K1 | 閒置時拔掉該 JetKVM 網路線，等 10 秒後插回；再送 `snapshot` | 拔線後下一次心跳就淘汰連線（Core log 有淘汰紀錄）；插回後 `snapshot` 自動重連成功 | | |
| K2 | `check_finish`（`timeout_sec` 120）長輪詢中拔網路線 | 不必等凍結門檻，立即回 `kvm_connect_failed` | | |
| K3 | K2 之後插回網路線，重新 `monitor_start` → 開測 → `check_finish` | 新一輪正常 `taken`，不需要重啟 Core | | |
| K4 | 閒置時讓 JetKVM 重開機 | 連線被淘汰；JetKVM 開機完成後下一個指令自動重連成功 | | |
| K5 | 測試進行中讓 JetKVM 重開機，開機後結果畫面已出現時送 `check_finish` | 回 `manual_verification_required`（斷線期間無法確認同輪） | | |
| K6 | 測試機畫面長時間靜止（例如 10 分鐘無變化）時送 `check_finish` | 不被誤判為斷線，不觸發重連 | | |

## KVM 重連後同輪重問（#56）

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| R1 | 取得 `taken` 後拔插 JetKVM 網路線，待重連後同輪再送 `check_finish` | 回 `already_taken`，結果與第一次相同 | | |
