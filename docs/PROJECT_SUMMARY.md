# B518 JetKVM + Log — 專案摘要

更新日期：2026-10-05（Asia/Taipei）

## 討論結論

- 本 repo 獨立承載 JetKVM 上位機程式、畫面辨識、HID 操作、設定、測試與文件。
- 程式責任為 HDMI 畫面串流、畫面狀態判讀、鍵盤／滑鼠操作與視覺結果回覆。
- 本專案辨識出的 Testing、PASS 與 FAIL 是獨立的正式視覺結果。
- 測試機本地 Log 的讀取與解析由另一個專案負責；本 repo 不讀取 Log、不設計 CSV schema，也不建立跨專案程式依賴。
- Arduino + Log 保留在既有 B518_205_207_ATE repo；兩者不共用工作樹或 Git 歷史。
- JetKVM 維持為 third_party/jetkvm submodule，來源為公司 private mirror；官方來源保留為 submodule 的 upstream remote。
- Repo 名稱雖保留 JetKVM_Log，僅為既有識別，不代表本 repo 承擔本地 Log 功能。

## 目前 Git 基準

- 方案分支：main
- JetKVM submodule：private mirror 的 dev 分支研究版本
  b3c29a44d9e2862b8ff7530830781803ce27b060

## Repo 拆分

- 上位機原始碼位於 host-app，保留既有執行時相對路徑。
- JetKVM CDC／TCP gateway 與 USB HID 相容性研究位於 docs；這些為 JetKVM 能力研究，不是本地 Log 讀取功能。
- 可重建的 build、dist、快取與壓縮檔不納入版本控制。

## Ticket 16：B518 Log Solution KVM 顯示契約受控整合（2026-10-05）

- 使用者明確指定本 repo `B518_JetKVM_Log` 為 Ticket 16 受控整合目標；本 repo 以 `main` commit `be41a2e09af9156f87ec2cac575041b8dc6c0ac4` 為基準建立 `codex/ticket-16`。App repo 獨立從 `B518-Log-Solution` commit `f2a7a014a9afb4fac8be1047d11c209aed68a1fe` 建立同名分支。
- `JetKVMClient` frame 增加遞增序號、單調接收時間、來源呈現時間戳、stream identity 與 thread-safe copied snapshot。`round_frame_consumer.py` 只吃原始 BGR frame，按 App 顯示契約 1.0 的兩個 locator、四種狀態 marker 及 1–20 格結果 band 執行定位和判讀；新輪容量鎖定於 Monitoring frame，較舊呈現時間的完成 frame 拒絕取用。
- DFU TCP `check` 從舊 4／7 格 Log OCR 改走顯示契約；移除舊 Log 結果 template/checker。外部 DFU 輸入視窗仍保留 4／7 格 profile，二者用途不同。review 回覆 pause；完成需先見過 Monitoring、兩張新鮮不同 frame、同容量／同狀態並全部是終態，並按設備隔離只取用一次。新回覆列為 `slot::STATUS`，畫面不含 SN；外部 TCP consumer 相容性及部署責任待確認。
- `python3 -m unittest discover -s tests -v`：第一批 30 tests passed；審查修正增加來源呈現時間戳倒退與輪次容量切換案例後，最新結果為 32 tests passed；包括容量 1／4／6／10／11／12／20、scale 1／1.5／2／2.25、rotation／遮擋／模糊／過期 frame、四種 marker、review pause、重連、新輪及一次性取用。
- `tools/verify_ticket16_app_frames.py` 以 Ticket 12 真實 Tk/Quartz 擷取的 5 張圖，經 prototype BGR frame API 得到 standby、monitoring、review pause、complete waiting、同輪第二張 complete take。證據與 JSON 報告位於 `docs/evidence/ticket-16/`；這是本機 Quartz 受控畫面，未冒充 JetKVM frame。
- 本機未安裝 Pillow、Quartz、aiortc 等 GUI/KVM dependencies，因此本次沒有啟動 prototype Tk／JetKVM UI，也未執行實際 JetKVM、設備動作、部署或發布驗收。現有上位機 TCP 呼叫端、維護／部署負責者與正式部署步驟尚未由 repo 文件定位。Ticket 12 AC 1 及 Ticket 13 AC 4 保持未通過。
- 首批實作 commit：`45258c9fccbdf39b32f7c069aa6fa4b6ae259a61`。固定基準雙軸審查提出時間戳／容量輪次隔離與型別註記問題，已加入來源 PTS 單調拒判、Monitoring 容量綁定與回歸測試；聚焦 15 tests、完整 32 tests、App Quartz frame replay 5 張均通過。複審、第二批 commit、push 與合併狀態待補。尚未授權以本機結果宣稱 Ticket 17／18 完成。
