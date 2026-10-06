# 6. 非同步等待：長輪詢與多工連線架構

Date: 2026-10-06

## Status
Accepted

## Context
原架構中，LabVIEW 若要等待耗時的測試結果，必須以極高頻率瘋狂發送 TCP `check` 指令。這不僅浪費頻寬，也迫使中繼站程式不斷抓圖與進行無效的影像辨識。另一方面，若採用單一連線處理所有設備，一旦某台設備卡在等待，將會阻塞其他設備的通訊。

## Decision
我們決定採用「長輪詢 (Long Polling)」搭配「設備專屬連線 (Per-Device Connection Multiplexing)」：
1. **長輪詢 (Long Polling)**：LabVIEW 端針對耗時指令送出帶有長 Timeout 的請求。中繼站收到後會將該 Socket 掛起（在 asyncio 迴圈中等待），並於背景默默監視影格。直到辨識到明確的 PASS/FAIL 或是逾時，才由同一個 Socket 回傳結果。
2. **多工連線 (TCP Multiplexing)**：LabVIEW 不共用 TCP Socket。每一台受控的機台（Device No），LabVIEW 必須為其開一條獨立的 TCP Connection 連接到中繼站。各機台的長輪詢、指令發送在各自的 Socket 上完全平行執行。

## Consequences
- **Pros**: 大幅降低不必要的網路封包與 CPU 影像辨識負載。LabVIEW 端與中繼站程式的邏輯最清晰，符合標準的分散式 I/O 處理慣例。
- **Cons**: LabVIEW 開發者需具備管理多條 Socket 連線陣列/集區的能力。
