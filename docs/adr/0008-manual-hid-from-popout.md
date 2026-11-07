# 8. Monitor UI pop-out 手動操作（修訂 #183 唯讀決定）

Date: 2026-11-06

## Status
Accepted（修訂 #183 的「pop-out 唯讀」；ADR-0004 的解耦不變）

## Context
ADR-0004 解耦後 Monitor UI 只透過 TCP 連 Core，沒有 KVM 連線，#183 因此把 pop-out 定為唯讀。現場（客戶看 BT 測試與 DFU 條碼輸入流程）需要直接在 pop-out 操控目標電腦，不想另開 JetKVM Web UI。`StreamerWindow` 本來就有完整的滑鼠／鍵盤轉送（`input_sender`）。

## Decision
1. Core 新增 device_bound 指令 `hid`（`method` 白名單 `absMouseReport`／`keyboardReport`／`wheelReport`，`params` 鍵與範圍嚴格驗證），經 KVM Connection Pool 的連線呼叫 `kvm.call`。Monitor UI 仍不直接連 KVM。
2. **與 Device Lock（ADR-0002）的關係**：`hid` 自己不上鎖（否則滑鼠移動會讓 LabVIEW 指令隨機 `device_busy`），`DeviceLockManager` 記錄持有鎖的指令名稱（`holder`）。**只在 Core 真的送 HID 時擋**：沒有持有者放行；持有者是 `check_start`／`check_finish`（只是在等畫面）且該設備 slot 的 `dock_recovering` 為 False 時放行；其他情況（`start_test`、`input`、`monitor_start`，或 check 正在點 Dock 恢復，`_long_poll` 以 try/finally 設定／清除 `dock_recovering`）回 `device_busy` 並丟棄（見 Consequences 的競態限制，並非絕對保證）。修訂（2026-11-07）：原本任何鎖都擋，現場 `check_finish` 長輪詢讀不到畫面時 pop-out 完全失效。它不碰輪次、閘門與 Freeze Guard 狀態。
3. Monitor UI 以獨立連線（`hid`）加背景佇列送出：滑鼠移動（按鍵遮罩相同）合併為最新一筆，按鍵／鍵盤／滾輪保序；設備 ID 於入列時固定；pop-out 固定綁定開啟時的設備（換設備須關閉重開）。佇列滿時只丟滾輪與無按鍵移動，按鍵／按鈕狀態變化不丟；關窗時已排隊的放開報告會送完。收到 `device_busy` 時清空佇列，但保留「已被 Core 確認按下的鍵／按鈕」的放開：把佇列中所有報告與已確認狀態取交集，合成每種最多一筆只釋放的報告（不會變成按下），並以 `release_only: true` 送出；Core 在送出當下再與目標機目前持有的狀態取交集（busy 晚到、Core 已先中立化時就什麼都不送），因此不會重新按下已被中立化的鍵，讓自動階段中放開並關窗的操作仍能到達 Core。
4. pop-out 底部提供「監看／手動控制」radio，預設監看（輸入全部不送出）；切到手動控制才轉送 HID，切回監看時放開所有按鍵。提示文字註明 Core 自動操作時手動操作會被忽略；收到 `device_busy` 時狀態列短暫顯示「手動操作已忽略」（顯示狀態翻轉才重繪，不隨每次滑鼠移動刷新）。

5. **自動接手時交接輸入狀態（#191 審查）**：Core 對每台設備記錄兩份鍵盤／滑鼠狀態：`remote`（目標機實際持有）與 `intent`（操作員最後要求，含自動階段被丟棄的報告）。自動指令（`start_test`／`input`／`monitor_start`，與 check 點 Dock）以 `_HandoverKvm` 包住 KVM，**在第一筆自動 HID 之前**才把 `remote` 釋放到中立（`call_confirmed`，有界等待 1 秒裝置回覆，成功才更新 `remote`）；預檢被拒絕或 Dock 找不到目標時零 HID（ADR-0005）。釋放失敗、被拒或逾時以 `hid_failed` 中止自動動作（零自動點擊、不帶修飾鍵），未釋放的部位保留重試。自動階段中被拒絕的報告只累計「放開」（`intent` 與其取交集，之後再按下也不會恢復）；自動階段結束時，把 `remote` **只往下釋放**到 `intent`（鍵碼與 modifier 取交集，例如 Shift+A 只鬆 A 會保留 Shift；滑鼠只比按鍵位元，單純移動不算）；**絕不重播自動階段中被拒絕的新按下**，也不在交接後重新按下（被拒絕的操作維持零 HID，ADR-0005／PROTOCOL §5.6）。每一輪只會縮小目標機持有的集合，因此迴圈不設固定輪數，一直做到某輪無事可做才結束（另有「沒有進展即停止」的不變量保護），且最後一次檢查與交還鎖／閘門之間沒有 await，補送等待期間被拒絕的放開一定會被後續一輪處理。補送期間手動 HID 仍被擋（`start_test` 等持鎖未放、check 的 `dock_recovering` 補送完才清），因此舊 ACK 不會覆蓋較新的狀態；補送若被取消，Device Lock 與 KVM lease 仍由內層 `finally` 歸還。注意：確認需裝置對每筆 RPC 回覆（JSON-RPC id）；現場尚未實測。
6. **RPC 錯誤歸屬（#191 審查）**：手動 HID 與釋放 RPC 使用獨立的 JSON-RPC id 空間（≥ 1,000,000,000），Core 自動動作的 id 在其下。回覆依 id 範圍歸屬，不靠會過期或滿載的暫存表，因此任何時間點晚到的手動錯誤都不會寫入共用 `rpc_errors`，也不會被 Dock 等 flow 誤判為自己的 `hid_failed`。

## Consequences
- **Pros**: 不需 JetKVM Web UI；預設不影響 LabVIEW 自動流程。
- **Cons**: 檢查鎖與送出之間有極小競態；LabVIEW 指令開始瞬間若恰有手動 HID，可能插入一筆報告。若自動流程開始時操作員正按住某鍵，放開報告會被丟棄，目標機按鍵可能卡住（失焦時 UI 會再送全放開）。check 長輪詢期間手動操作放行，若此時 check 恰好開始點 Dock，兩者的 HID 報告可能交錯（check 的 Dock 點擊約數百毫秒）；操作員在 `start_test`／`input` 期間不應操作 pop-out。
