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

## 修訂（2026-10-16）
原決定的凍結判斷以「同一串流的 PTS 停滯」為準，但未區分 KVM 重連後的新串流。重連後新串流的 PTS 從低值重新開始，舊基準會讓新串流被誤判為停滯而回 `frame_frozen`，直到新 PTS 超過舊值為止（可能數小時）。

修訂後的規定：
1. 每台設備的凍結狀態記錄所屬串流編號（`stream_id`）。檢查路徑必須由呼叫端明確傳入本次檢查的串流編號。
2. 串流編號改變時，重置該設備的凍結狀態，以新串流第一張畫面為新基準；新串流首張畫面無 PTS 時，不以 None 作為基準，等待下一張有 PTS 的畫面。
3. 同一串流的 PTS 停滯或倒退，達門檻仍回 `frame_frozen`，原規定不變。
4. 凍結狀態只影響該設備，不影響其他設備。

原文保留，不刪除原規定。

## 修訂（2026-10-20）
1. 修訂 2026-10-16 第 1 點的「呼叫端必須明確傳入串流編號」：串流編號由影格快照（`FrameSnapshot.stream_id`）帶入，每台設備一個凍結狀態自行比對；換串流重置基準的規定不變。原先兩套凍結判斷（Freeze Guard 與 RoundFrameGate）合併為一套。
2. 沒有 PTS 一律回 `frame_frozen`（message 註明無 PTS）。
3. 凍結檢查擴及送 HID 的指令：`input`、`start_test`、`monitor_start` 送 HID 前檢查一次，凍結時回 `frame_frozen` 且不送 HID。
4. PTS 只證明串流活著，無法偵測測試電腦或 Log Solution 當機（HDMI 仍持續輸出、PTS 仍前進）；該情境由「Log Solution 心跳」處理（見 CONTEXT.md、1.2 建議 C8）。

## 修訂（2026-10-26）
1. 移除凍結判定超過 5 秒即不再顯示 FROZEN 的規則。設備一旦判定畫面凍結，`core_status` 與 `status` 持續保留該設備的凍結狀態，直到同一設備再次觀察到 PTS 前進才解除，不需人工確認。
2. 換串流仍重置 PTS 比較基準，但不解除狀態回報中的凍結。新串流首張影格只建立基準，第一次 PTS 前進才解除凍結。
3. 送 HID 前的主動窗口（`FrameStream.wait_live`）、`FrameStream.is_frozen` 與輪次閘門的判斷規則不變；本次修訂只調整狀態回報的凍結保留方式。
4. 只有讀取影格的指令（`check_start`、`check_finish`、`inspect`、`input`、`start_test`、`monitor_start`）能觀察 PTS 前進並解除凍結；`status`／`core_status` 不讀取影格，輪詢不會解除。KVM 重連換串流後至少需兩次讀取：首張建立基準，後續須有 PTS 前進。LabVIEW 應送唯讀的 `inspect` 重新判斷，必要時重複送出，不應只輪詢 `status` 等待 `frozen=false`。
5. KVM 斷線不會解除凍結。唯一例外範圍很窄：某次讀取判定 FROZEN，而該次讀取的回覆是 `kvm_connect_failed`（不是 `frame_frozen`；包括讀取超過 inspection timeout、在背景完成時 KVM 已斷線的那次讀取），只撤銷這個判定剛新增的凍結 latch。理由：LabVIEW 從未因這次讀取收到 `frame_frozen`，而且這個凍結判定可能只是 KVM 在讀取途中斷線造成的假象。同一 latch 一旦有任何命令已向 LabVIEW 回覆 `frame_frozen`，就不再適用此例外，即使建立 latch 的原讀取後來回覆 `kvm_connect_failed` 也必須保留，直到 PTS 前進。斷線前就已存在的凍結 latch 一律保留。
6. 已鎖定凍結的設備後續 KVM 斷線時，依使用者決定（2026-10-26）繼續顯示凍結：`status` 維持 `frozen=true`、`connected=false`，`core_status` 的 `stations[].status` 為 `FROZEN`、`is_connected=false`；Monitor UI 該列維持紅底 Frame frozen、紅燈不閃，狀態文字不變；列上的 KVM 連線小燈照當下 `is_connected` 顯示：斷線為 Offline（白色 3px 空心圈，中間不填色），重新連線回到 Online；整列 tooltip 另寫出 KVM 連線狀態（使用者決定 2026-10-26 選 B，#144；所有紅色列一律照實際連線顯示小燈，取代原先「列上不顯示 Offline、連線狀態只放在 tooltip」的說法）。凍結只在畫面重新前進（PTS 前進）時解除，目的是讓人看得出設備在斷線前已經異常。
7. 同一串流 PTS 倒退時，依 2026-10-16 第 3 點，須等 PTS 超過原值才算前進；凍結 latch 持續顯示直到此時。
8. 凍結 latch 保存在記憶體，Core 重啟會清除，但不代表設備已修復。

原文保留，不刪除原規定。
