# 5. 畫面凍結防呆 (Frame Freeze Guard)

Date: 2026-10-06

## Status
Accepted

## Context
在依賴視覺判讀（Pattern Matching 與 OCR）的自動化設備（如 FCT、BT）中，如果測試機的程式當機或 HDMI 輸出卡死，畫面可能永遠停留在上一次測試的 "PASS" 狀態。如果中繼站程式不做校驗，會直接取用這個死當畫面，導致當前測試誤判為 PASS。

## Decision
我們決定將「畫面活躍性檢查（Frame Freeze Guard）」強制推廣到所有站別：
1. **訊號來源**：統一依賴 JetKVM WebRTC 底層傳回的影格時間戳（Presentation Timestamp, PTS）或流水號。
2. **防呆觸發**：在執行任何視覺 `check` 指令前，中繼站必須監測該 KVM 的時間戳。如果時間戳超過指定秒數沒有遞增，或是未能提供新鮮的連續影格，必須強制中斷分析並回傳 `error: screen frozen` 給 LabVIEW。

## Consequences
- **Pros**: 完全避免因機台當機造成的產線良率假象（False Positive）。直接讀取硬體層時間戳，零 CPU 效能損耗。
- **Cons**: 需修改所有既有 `auto_flow` 與 `check` 的底層抓圖邏輯，全面導入 `RoundFrameGate` 機制。
