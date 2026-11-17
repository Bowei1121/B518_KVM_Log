# 2. 按設備分流的鎖機制與併發 Fail-Fast 策略

Date: 2026-10-06

## Status
Accepted

## Context
原本 `ui_app.py` 處理 TCP 指令時使用了一把全域的 `_cmd_lock`。當一個設備正在執行耗時指令（如 `check` 輪詢或等待輸入完成）時，所有來自 LabVIEW 其他台設備的請求都會被排隊阻塞，這使得並行測試成為不可能，並會造成 TCP 封包的嚴重堆積與逾時。

## Decision
1. **Per-Device Lock**：取消全域鎖，改為以單台 KVM（Device No 或 KVM IP）為層級的專屬鎖。各設備的指令處理完全並行互不干擾。
2. **Fail-Fast**：針對**同一台設備**，若前一個指令尚未完成，又收到下一個指令（例如同時發出 input 與 check），中繼站不進行排隊，而是直接回傳 Busy 錯誤。迫使 LabVIEW 端修正其狀態機流程，避免累積陳舊狀態。

## Consequences
- **Pros**: 完美支援多 KVM 平行測試。能提早暴露 LabVIEW 端不合理的非同步指令序列。
- **Cons**: LabVIEW 必須有完善的狀態管理機制，不能隨意向正在忙碌的 KVM 連發指令。

## 補充：start_round 內部等待（#212 契約，待 #213 實作）

`start_round` 可在持有單一設備鎖期間進行共用截止時間的有界內部等待與安全重試；其他外部同設備衝突仍立即回 `device_busy`，包括 `input`／`check_finish`。不同設備獨立，不使用全域鎖。指令路徑 KVM 建立連線既有行為沒有時間上限，是否納入截止時間待 #213 或後續釐清；詳見 PROTOCOL 5.6a。
