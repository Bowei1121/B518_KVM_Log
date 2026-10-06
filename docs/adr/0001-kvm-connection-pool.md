# 1. 引入 KVM 連線池與自動保活淘汰機制

Date: 2026-10-06

## Status
Accepted

## Context
原有的 `ui_app.py` 中只有單一的 `JetKVMClient` 實例，當 LabVIEW 要求不同 IP 的測試機畫面時，中繼站會中斷現有連線並啟動新的 WebRTC 連線（包含 Signaling 與 ICE 打洞）。這導致切換設備時產生高達數秒的延遲，嚴重影響多設備並行測試的產線效能，且頻繁的連線中斷容易觸發連線狀態不同步的系統錯誤。

## Decision
我們決定引入 `KVMConnectionPool` 來常駐管理多台 KVM 連線：
1. **閒置淘汰 (Idle Timeout)**：連線閒置超過 10 分鐘（未收到 LabVIEW 指令）時，主動斷開連線以節省上位機與 KVM 資源。當新指令進來時自動重新連線。
2. **自動保活 (Heartbeat)**：對於正在使用中的連線，在閒置幾秒後自動於背景觸發抓取影格，確保 WebRTC 通道暢通並偵測 KVM 異常斷線。

## Consequences
- **Pros**: 顯著消除切換測試機時的 WebRTC 重連延遲，提升並行吞吐量。增強連線的強健性與可預測性。
- **Cons**: 需增加背景執行緒或非同步迴圈對連線生命週期的管理複雜度。
