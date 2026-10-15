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
