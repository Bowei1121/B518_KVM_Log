# 4. Core Service 與 UI 的解耦及生命週期管理

Date: 2026-10-06

## Status
Accepted

## Context
原架構將 TCP 伺服器、WebRTC 影像連線與 Tkinter GUI 綁在同一個 `ui_app.py` 行程內。當 GUI 卡頓、拖曳或意外當機時，會導致背景 TCP 與影像擷取完全停擺；且在產線上，UI 不應該是系統運行的必要條件。同時，為避免解耦後背景程序變成無法正常關閉的殭屍進程（Zombie Process）而鎖死 TCP 端口。

## Decision
1. **實體解耦 (Physical Decoupling)**：將核心的 `JetKVM_Log` 功能封裝成無介面的純背景系統服務（Core Service）。Tkinter UI 退化為僅僅是用於觀測與維護的 Monitor Client，透過本機 TCP 連接 Core Service。
2. **優雅關閉 (Graceful Shutdown)**：在 TCP API 中實作專屬的 `{"cmd": "shutdown"}` 指令。LabVIEW 或 Monitor UI 可發送此指令，Service 收到後主動中斷所有 KVM 連線並釋放 Socket。
3. **Daemon 清理腳本**：提供獨立的批次檔 (Batch Script) 作為最後的防線，當異常發生時能一鍵強制清理殘留的 Core Process。

## Consequences
- **Pros**: 提升產線運行的系統強健性，UI 崩潰或被關閉不再影響正在進行的測試通訊。
- **Cons**: 需維護獨立的啟動/關閉腳本，架構從單一行程變為 Client-Server 架構。
