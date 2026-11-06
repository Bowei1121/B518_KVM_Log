# 8. Monitor UI pop-out 手動操作（修訂 #183 唯讀決定）

Date: 2026-11-06

## Status
Accepted（修訂 #183 的「pop-out 唯讀」；ADR-0004 的解耦不變）

## Context
ADR-0004 解耦後 Monitor UI 只透過 TCP 連 Core，沒有 KVM 連線，#183 因此把 pop-out 定為唯讀。現場（客戶看 BT 測試與 DFU 條碼輸入流程）需要直接在 pop-out 操控目標電腦，不想另開 JetKVM Web UI。`StreamerWindow` 本來就有完整的滑鼠／鍵盤轉送（`input_sender`）。

## Decision
1. Core 新增 device_bound 指令 `hid`（`method` 白名單 `absMouseReport`／`keyboardReport`／`wheelReport`，`params` 鍵與範圍嚴格驗證），經 KVM Connection Pool 的連線呼叫 `kvm.call`。Monitor UI 仍不直接連 KVM。
2. **與 Device Lock（ADR-0002）的關係**：`hid` 自己不上鎖（否則滑鼠移動會讓 LabVIEW 指令隨機 `device_busy`），但設備鎖被持有時回 `device_busy` 並丟棄。手動 HID 在鎖持有期間會被丟棄（見下方 Consequences 的競態限制，並非絕對保證）。它不碰輪次、閘門與 Freeze Guard 狀態。
3. Monitor UI 以獨立連線（`hid`）加背景佇列送出：滑鼠移動（按鍵遮罩相同）合併為最新一筆，按鍵／鍵盤／滾輪保序；設備 ID 於入列時固定；pop-out 固定綁定開啟時的設備（換設備須關閉重開）。佇列滿時只丟滾輪與無按鍵移動，按鍵／按鈕狀態變化不丟；關窗時已排隊的放開報告會送完。收到 `device_busy` 清空佇列。
4. pop-out 底部提供「監看／手動控制」radio，預設監看（輸入全部不送出）；切到手動控制才轉送 HID，切回監看時放開所有按鍵。提示文字註明自動測試指令執行中時操作會被忽略。

## Consequences
- **Pros**: 不需 JetKVM Web UI；預設不影響 LabVIEW 自動流程。
- **Cons**: 檢查鎖與送出之間有極小競態；LabVIEW 指令開始瞬間若恰有手動 HID，可能插入一筆報告。若自動流程開始時操作員正按住某鍵，放開報告會被丟棄，目標機按鍵可能卡住（失焦時 UI 會再送全放開）。操作員在 LabVIEW 流程中不應操作 pop-out。
