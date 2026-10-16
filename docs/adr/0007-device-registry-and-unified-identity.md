# 7. 設備清單與統一設備 ID

Date: 2026-10-15

## Status
Accepted（spec #1 實作完成，2026-10-15；見 `docs/specs/0001-device-identity-protocol-1.0.md`）

## Context
各模組各自組合設備身份：Device Lock 只用 `device` 值（DFU／FCT／BT 預設都是 `"1"`，三站互相 BUSY）；Frame Freeze Guard 與 DFU RoundFrameGate 用 `站別:設備號`；`run_check` 內的凍結檢查只用站別；KVM Connection Pool 用 IP；`snapshot` 沒帶 IP 時以「站別或設備號任一符合」猜設定，FCT:1 會拿到 DFU 的 IP。站別清單在 `tcp_server.py` 與 `ui_app.py` 各寫死一份，DFU 4／7 格另存 `device_profiles.json`。LabVIEW 需記住並傳送每台 KVM IP。

## Decision
1. **設備 ID**：每台設備一個固定、跨站唯一的 ID，格式 `<站別>-<兩位數>`（B518：`DFU-01`～`DFU-07`、`FCT-01`、`BT-01`），區分大小寫嚴格比對。一台設備 = 一台測試機 + 一台測試機電腦 + 一台 JetKVM。
2. **設備清單**：上位機 `~/Documents/template/devices.json` 是唯一來源，記錄設備 ID → 站別、KVM IP、測試通道數。取代程式內寫死清單與 `device_profiles.json`。內容錯誤（ID／IP 重複、格式錯、站別與 ID 前綴不符）或檔案不存在時 Core 拒絕啟動。修改後重啟 Core 才生效。
3. **協定只帶設備 ID**：LabVIEW 不傳 `kvm_ip`、`station`，帶了即拒絕；IP 與站別由 Core 查表。動作指令缺少或使用未知設備 ID 即拒絕。詳見 `docs/PROTOCOL.md`。
4. **單一 key**：Device Lock、Frame Freeze Guard、RoundFrameGate、匹配診斷目錄與後續輪次保存一律以設備 ID 為 key；KVM Pool 以查表後的 IP 連線，一對一保證鎖設備即鎖 KVM。
5. **測試通道數**：每台設備設定通道數 N（B518 全部為 4）。DFU 輸入在視窗內找出全部 checkbox，數量須等於 N，依由上而下、由左至右排序為通道 1～N；通道編號與 Log Solution Slot 一致。`check` 取得結果時畫面格數必須等於 N。
6. **Monitor UI**：移除手動 IP，改從 Core 設備清單選擇；新增設定頁直接編輯本機 `devices.json`（同機部署），儲存前驗證與備份，任一設備執行動作時禁止重啟 Core。手動檢查不上鎖、不消耗本輪結果。
7. **網路**：EKI-2720G 為非網管交換機，每台 JetKVM 與上位機手設靜態 IP。

保留 ADR-0002 fail-fast、ADR-0003 JSON 不向下相容、ADR-0006 每設備一條連線。

## Consequences
- **Pros**：跨站 BUSY 與連錯 KVM 的問題消失；LabVIEW 不需管理 IP；換 KVM 或改 IP 只改一個檔；格數核對可攔截 Log Solution 設定錯誤或錯台畫面。
- **Cons**：LabVIEW 與 Core 需同步改版（協定 1.0 不相容舊格式）；JetKVM 靜態 IP 需確認韌體支援；設定錯誤會讓 Core 無法啟動，需靠設定頁驗證降低風險。
- 輪次 UUID、ACK、結果保存另案設計，以設備 ID 為 key。
