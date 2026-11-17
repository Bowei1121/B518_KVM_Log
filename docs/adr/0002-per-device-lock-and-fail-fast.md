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

`start_round` 可在持有單一設備鎖期間進行共用截止時間的有界內部等待與安全重試；其他外部同設備衝突仍立即回 `device_busy`，包括 `input`／`check_finish`。不同設備獨立，不使用全域鎖。使用者 2026-11-17 定案：只對 `start_round` 加整體截止，KVM 連線與 ICE 等待計入 T=30 時的 110 秒硬上限，LabVIEW TCP Read 145 秒不變；由 #213 實作，不動共用連線池。截止時須正確取消／釋鎖，不可拖住其他設備。

同一輪不重設 T 或 D，D = T + 73 秒；poll timeout 在共用 T 內預先分配最多三次嘗試（含首次），每次最多 T/3，最後一次使用當時剩餘的 T，0.5 秒間隔計入同一 T／D，總等待不得超過 T，不得挪用 73 秒當額外 T。只重讀畫面、繼續等待，不重新呼叫 `monitor_start`、不再點 Dock、不再送快捷鍵（含 Cmd+Shift+M）。監控中即本階段成功、不得當成 `log_solution_busy`；待命只繼續觀察，所有嘗試用盡仍為待命則 `status=error`、`kind=fault`，保留 `monitor_not_started`；待確認立即停止並回 `status=error`、`kind=paused`、`error=log_solution_review`。

開測 HID 送出前，次數用完保留最後原碼，T 用完保留 `monitor_not_started`，D 截止有原碼則保留原碼，均回 `status=error`、`kind=fault`；只有 D 截止且無原碼時回 `status=timeout`、`error=timeout`、`kind=fault`。開測 HID 送出後（包含回覆遺失），須在剩餘 D 內觀察本輪至少一個通道的可信測試活動或有效結果才回 started，`start_test` 成功及監控就緒均不足以確認；確認窗口即剩餘 D。D 截止或確認窗口結束一律回 `status=error`、`kind=paused`，error 保留原碼，無原碼則用 `hid_failed`，不自動重送。以上均為使用者 2026-11-17 定案，詳見 PROTOCOL 5.6a。
