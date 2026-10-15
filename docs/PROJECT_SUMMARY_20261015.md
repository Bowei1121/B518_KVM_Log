# B518 JetKVM — 專案摘要（2026-10-15）

日期：2026-10-15（Asia/Taipei）

## 前次狀態（摘自 `PROJECT_SUMMARY_20261014.md`）

- 2026-10-14 已完成 setup-matt-pocock-skills：`AGENTS.md`、`docs/agents/issue-tracker.md`（GitHub `Bowei1121/B518_KVM_Log`，`gh` 必須帶 `--repo`）、`triage-labels.md`（五個預設標籤）、`domain.md`（single-context：`CONTEXT.md` + `docs/adr/`）。
- 架構評估提出 5 個候選，首選「統一設備識別與 Device Lock」，其次「收攏各站別視覺判定」；尚未選定、未實作。
- grill-with-docs Q1–Q18 完成，`docs/LABVIEW_LOG_SOLUTION_INTEGRATION.md` 需求已確認：Log Solution 為 DFU/FCT/BT 正式結果來源、Slot 1–20 編號、非 PASS 處置、LabVIEW 輪次 UUID 僅限橋接 TCP、重啟恢復、ACK 與 14 天保留。尚未實作或現場驗收。

## 本次確認

- 使用者要求確認 setup-matt-pocock-skills 是否已建立。檢查結果：所有設定檔均存在且內容與 `AGENTS.md` 一致，無需重建。
- 已閱讀 `CONTEXT.md`、ADR-0001～0006、整合規格與 `host-app/` 主要模組結構：
  - `core_daemon.py` 程序管理；`tcp_server.py` JSON Protocol 與預設 handler；`device_lock.py`、`kvm_pool.py`、`freeze_guard.py` 對應 ADR-0002/0001/0005；`round_frame_consumer.py` 顯示契約 1.1 判讀；`dfu_flow.py` DFU 輸入；`ui_app.py` Monitor UI client。
- 本次未修改程式、未執行測試、未連實機。

## 下一步候選

- 從架構候選中選定一項進入 grilling／實作（首選設備識別與 Device Lock）。
- 依整合規格設計輪次 UUID、結果保存、ACK 的協定欄位與實作次序，並建立 GitHub issues。

## grilling：架構候選 1「統一設備識別與 Device Lock」

已確認（Q1–Q15）：

- 設備 ID 採跨站唯一固定 ID：`DFU-01`～`DFU-07`、`FCT-01`、`BT-01`；嚴格比對，LabVIEW 端嚴格規範。
- ATE 組成為測試機＋測試機電腦＋JetKVM 一對一；9 台 JetKVM 與上位機接同一台研華 EKI-2720G（非網管型，無 DHCP）。
- 上位機 `~/Documents/template/devices.json` 為唯一設備清單（ID → 站別、KVM IP、DFU 4／7 格），取代程式內寫死清單與 `device_profiles.json`；錯誤即拒絕啟動；修改後需重啟 Core。
- 指令只帶設備 ID，帶 `kvm_ip`／`station` 即拒絕；改用 JSON，舊逗號格式與 `TCPIP_test.vi`、README 範例需更新。
- 每台 JetKVM 與上位機手設靜態 IP（建議上位機 .10、DFU .70–.76、FCT .77、BT .78）；JetKVM 正式版是否有靜態 IP 選項待現場確認（社群資料指 0.5.0 dev 起支援）。
- `req_id` 由 LabVIEW 產生且必填，Core 原樣回傳；不是輪次 UUID。
- 動作指令缺設備 ID 或 ID 錯誤即拒絕；長 `check` 期間同設備其他動作回 BUSY（ADR-0002）。
- Monitor UI 拿掉手動 IP，改選設備；另需設計設定頁面。
- 輪次 UUID／ACK／14 天保存另案討論。
- 已產出 `docs/PROTOCOL.md` 1.0 草案（固定 envelope、固定 data 欄位、錯誤碼、LabVIEW typedef 清單、與現行程式落差）。

查證：Log Solution 顯示契約 1.1 已有 `standby／monitoring／review／complete` 四種標記；橋接可辨識「監控中」與「待確認」，但現行長輪詢遇待確認不會立即回覆，`input`／`button` 尚未接上 Core JSON。

### 第 4 輪（Q16–Q21）

- Q16：Log Solution 為 `monitoring`／`review` 時 `input` 回 `log_solution_busy`。
- Q17：`COMPLETING` 繼續等；`STOPPED`（人員手動停止監控）使用者傾向判 FAIL，處置方式待下一輪確認。
- Q18：設定頁面已提供模擬圖，待使用者決定修改方向。Q19：Monitor UI 與 Core 同機。
- Q20：同意嚴格欄位、`items` 陣列、只看逐格 `results`；PROTOCOL.md 已補 `timeout_sec` 白話說明與全指令 JSON 範例。
- Q21：FCT／BT 過渡期保留模板判讀並套用同一 `CheckResp`；模板判讀改善空間待討論。

### 模板判讀盤點（子代理唯讀調查，主線已核對關鍵點）

- Core `check` 只有請求帶 `template_root` 才跑 OpenCV（`tcp_server.py:329`）；LabVIEW 與 Monitor UI 都不帶，因此 FCT／BT 在 Core 上實際不做模板判讀：無 timeout 時只憑畫面有在動就回 `PASS`，長輪詢則逾時回 `NONE`。
- 即使跑模板：灰階比對、全域門檻 0.8、結果列用原尺寸 `find_all`（縮放約 ±5% 以上就找不到）、找不到任何標記時判 `PASS`、單張畫面決定、例外被吞掉後落到 `PASS`；凍結檢查在 `run_check` 內以站別為 key，多台 BT 共用狀態。
- 列判讀、空結果→PASS、例外→PASS 等路徑皆無測試。
- 待模板判讀議題討論時處理；尚未修改程式。

### 第 5 輪（A18、A20、A22–A25）

- A18／A23：設定頁採模擬圖方向；「DFU 輸入」改為「測試通道數」可手動輸入數值（不同專案通道數不同）；按鈕採圖示＋簡短文字；雙語需求列後續。
- A20：`shutdown` 不給 LabVIEW；LabVIEW 需要顯示連線狀態（以 `devices` 或 `status` 提供，待確認）。詢問 9 台同時長輪詢效能。
- A22：`STOPPED` 判不良、交作業員重工；PROTOCOL.md `results[].status` 已加入 `STOPPED`，原樣回傳由 LabVIEW 處置。
- A24：模板判讀改善留待下一議題。A25：現場將全面使用 Core 版；同意先修 FCT／BT `check` 看不到結果仍回 PASS 的問題。

效能實測（本機 i7-1068NG7，`inspect_app_frame` 1080p 單張）：一般 UI 畫面 0.8–5.6 ms；高紋理／雜訊畫面最差約 3.5 s。判讀在 asyncio 事件迴圈中同步執行，慢畫面會卡住所有設備與 TCP。9 路 WebRTC 解碼負載未量測。
