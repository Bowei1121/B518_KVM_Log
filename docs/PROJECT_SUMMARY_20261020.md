# 專案摘要 2026-10-20

接續 `PROJECT_SUMMARY_20261019.md`「第二輪架構評估」。本檔記錄換日時的進度與流程，供後續查閱。

## 前一日結論（2026-10-19）

- 第二輪架構評估：7 個候選；使用者選 C1～C4 grilling（Q1–Q29），開票 #54～#59，另開 #63（tests recv 迴圈收尾）。決策細節見 20261019 摘要。
- CONTEXT.md 新增「設備輪次」；`docs/FIELD_VERIFICATION.md` 集中所有待現場驗證項目（開發階段無法實機驗證）。
- 依檔案重疊重排順序：#54 改為 blocked by #57（`read_app_frame` 是取影格與讀畫面的交界，語意衝突）。

## 開發流程（使用者 2026-10-19 規定）

- 實作：獨立 session，Haiku 5.5，effort high。
- 審查：另一個獨立 session，Opus 5.5，effort medium，用 `/code-review`。
- 迴圈：實作 → 審查 → PR comment → 修正 → PR comment（逐點說明）→ 再審查 → … 直到審查結論「可合併」才 merge。
- merge 由主 session 執行，並同步 Gitea `origin` 的 main（`origin` 是 http，app 的同步工具不支援，改手動 push）。

## 票況（2026-10-20 收尾）

全部完成，main 在 `4e24a0e`，GitHub 與 Gitea 同步；整合後 main 全套 **589 passed, 34 subtests**。

| 票 | PR | 審查輪次 | 合併 |
| --- | --- | --- | --- |
| #57 具名影格快照 | #62 | 審查 → 修正（freeze_guard 不吞例外、`read_app_frame` 回 `(snapshot, observation)`） | `0511cfd` |
| #59 core_protocol（Grok） | #61 | 審查 → 審查 session 自行修 `_pending` 並合併（未經再審） | `1b82ee9` |
| #54 Log Solution 讀取 | #65 | 審查（key 順序）→ 修正 → 再審「可合併」 | `b9e8066` |
| #63 tests 收發收尾 | #67 | 審查「可合併」 | `5d2db24` |
| #58 KVM 斷線偵測 | #66 | 審查（3 項）→ 修正 → 再審「可合併」 | `a970e76` |
| #55 DeviceRound | #68 | 審查（缺 SN 放行測試）→ 修正 → 再審「可合併」（含與 #66 試合併） | `4edb0b9` |
| #56 重連後同輪重問 | #70 | 審查「可合併」（7 個 mutation 皆被抓到） | `4e24a0e` |

新開：**#69**（`needs-info`）KVM 拔線偵測時間（ICE consent 逾時約 24～36 秒，推論）是否縮短，待現場 K1 實測。

## 後續

- 到現場依 `docs/FIELD_VERIFICATION.md` 執行 D1～D7、K1～K7、R1，並回填 #69。
- 第二輪評估未開票的候選：C5 CoreChannel worker 佇列、C6 指令描述表、C7 HID 對位點擊原語；C3 後續的兩套凍結判斷合併（見 #57 票「後續事項」）。
- 協定 1.1（輪次 UUID、ACK、持久化、14 天保留）仍未開始。

## 觀察

- 子 session 有時不依指示行事：兩個早期審查 session 未詢問即貼 comment；#59 審查 session 自行修正並合併。現行流程已明文授權貼 comment，但 merge 只由主 session 執行。
- 子 session 可能不採信主 session 轉達的跨 session 訊息（#57 session 曾暫停）；brief 內預先寫明流程可避免。
- 自動事件觸發的回合不能直接啟動新 session，只能提出啟動卡，需使用者點開。

## 第三輪架構評估（2026-10-20）

- 依 commit 熱點（`tcp_server.py` 30 次、`round_frame_consumer.py`、`monitor_flow.py`、`ui_app.py`）由子代理唯讀探索。HTML 報告存於 OS temp（`architecture-review-20261020-090513.html`，可能被系統清理）。
- 6 個候選：C1 FrameStream（合併兩套凍結判斷）、C2 RoundCheck＋DeviceSlot、C3 指令描述表（上輪 C6）、C4 HidSession（上輪 C7）、C5 死碼與進入點清理、C6 CoreChannel worker 佇列（上輪 C5，不開票）。使用者選 C1～C5 grilling（Q1–Q33）。

### 關鍵發現

- `input`、`start_test` 送 HID 前只看影格是否過舊，`monitor_start` 先點 Dock 與快捷鍵才經過凍結判斷：凍結畫面上仍會送 HID（推論，未實機重現）。
- 沒有 PTS 時 Freeze Guard 回 `frame_frozen`、RoundFrameGate 回 `unknown`；status 的 FROZEN 只在 check/inspect 時更新。
- PTS 只證明 JetKVM 串流活著；Mac 或 Log Solution 當機時 HDMI 仍輸出、PTS 仍前進，現有設計抓不到。使用者提出在 Log Solution 畫面加計時器 → 定為「Log Solution 心跳」。

### grilling 決策

- C1 拆兩票：重構（FrameStream 單一狀態；long-poll 被動追蹤、一次性指令主動窗口且最近有前進即放行；無 PTS 一律 `frame_frozen`；status 回最後判定與時間、過舊顯示未知；inspect 可更新串流狀態）＋修 bug（三個 HID 指令送 HID 前檢查一次，凍結回 `frame_frozen` 不送 HID）。ADR-0005 加 2026-10-20 修訂。
- C2：同步 `step(reading, now)`、mode enum 取代兩旗標、刪 `accept_round`、DeviceSlot 併入；reading 由 long-poll 先問 FrameStream 再組；既有 socket 測試不改全綠即驗收。
- C3：CommandSpec 一張表；handler 收 `(ctx, args)`；`core_status` 改 `core_version`；排最後。
- C4：HidSession（find／click／focus_dock）、`FlowError` 單一錯誤類別、拆 `auto_flow`、刪 `hid_helpers`；點擊前不檢查凍結。
- C5：唯一進入點 `core_daemon`；刪死碼（連同只測死碼的測試）；`tcp_round_reply` 移到 `tools/`；debug 腳本刪或移 `debug_tool/`。
- 心跳：4 格黑白 16 態計數、每 0.5 秒 +1（一圈 8 秒 > 3 秒門檻，避免兩態閃爍的 aliasing），App 活著就跳；橋接只比對變化；可讀但 3 秒不變回 `log_solution_unresponsive`，讀不到走 Dock 補救；順序 KVM 健康 → 串流凍結 → 心跳 → 判讀；無方塊則跳過。另有給人看的「本輪經過時間」。寫入 1.2 建議 C8 與 HANDOFF 第 13 節（HANDOFF 副本已更新，待 Log Solution 主文件同步）；方塊在狀態格與右定位點之間放不下，座標待 Log Solution 決定。
- CONTEXT.md 新增「Log Solution 心跳」「本輪經過時間」，Frame Freeze Guard 補充 PTS 的能力範圍與 inspect 唯讀釋義。

### 開票（不開母票，原生 blocked-by）

| 票 | 內容 | 相依 | 實作 |
| --- | --- | --- | --- |
| #71 | C5 清理 | — | Grok |
| #72 | C4 HidSession | — | Grok |
| #73 | C1 重構 FrameStream | #71 | Haiku |
| #74 | C1 修 bug：凍結畫面仍送 HID | #73、#72 | Haiku |
| #75 | C2 RoundCheck＋DeviceSlot | #73 | Haiku |
| #76 | C3 CommandSpec | #75、#74 | Haiku |
| #77 | 心跳（`needs-info`，等 Log Solution 座標） | #73 | — |

順序：第 1 波 #71 ∥ #72 → 第 2 波 #73 → 第 3 波 #74 ∥ #75（monitor_flow 小重疊，保守則 #74 先）→ 第 4 波 #76。#71、#72 已留言：不准自行 merge、用主 repo venv、push 兩個 remote、限定檔案範圍。審查一律 Opus session，merge 由主 session。

### 第 1 波結果（同日）

- Grok 實作並經 Grok 複審：PR #79（#72 HidSession）、PR #80（#71 清理）。Claude（Opus 子代理）再複審：兩者皆「可合併」，兩 PR 試合併全套 **582 passed、34 subtests**；已貼 PR comment 並合併，main `48de3c8`，Gitea 同步。
- 接受的行為差異（#79）：DFU 多格 HID 錯誤遇第一個即停（message 取第一個錯誤）；點擊 HID 失敗後不再打 SN。
- 後續票：#81 HidSession 點擊前清錯吞錯、鍵盤方法自行檢查（blocked by #74）；#82 測試寫入真實 `~/Documents/template` 的並行 flaky、刪 `device_lock._owners`。
- `7bc235f` 誤帶入 repo 根的 `g71.md`、`g72.md`（Grok 留言草稿）已刪除。
- Log Solution 交來心跳交接稿（Log Solution repo `docs/refactoring/LOG_TIMER_HEARTBEAT_CLAUDE_HANDOFF_2026-10-20.md`、ADR0012）：異常條件改為「連續三張有效同圖」、狀態格左移、精確座標待 App 實作；#77 第四輪 grilling（Q34–Q41）進行中。
