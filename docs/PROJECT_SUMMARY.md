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
- DFU TCP `check` 從舊 4／7 格 Log OCR 改走顯示契約；移除舊 Log 結果 template/checker。外部 DFU 輸入視窗仍保留 4／7 格 profile，二者用途不同。review 回覆 pause；完成需先見過 Monitoring、兩張新鮮不同 frame、同容量／同狀態並全部是終態，並按設備隔離只取用一次。新回覆列為 `slot::STATUS`，畫面不含 SN；外部 TCP consumer 相容性待確認；使用者後續確認維護及提供更新由使用者負責，當地 TE 協助部署。
- `python3 -m unittest discover -s tests -v`：第一批 30 tests passed；審查修正增加來源呈現時間戳倒退與輪次容量切換案例後，最新結果為 32 tests passed；包括容量 1／4／6／10／11／12／20、scale 1／1.5／2／2.25、rotation／遮擋／模糊／過期 frame、四種 marker、review pause、重連、新輪及一次性取用。
- `tools/verify_ticket16_app_frames.py` 以 Ticket 12 真實 Tk/Quartz 擷取的 5 張圖，經 prototype BGR frame API 得到 standby、monitoring、review pause、complete waiting、同輪第二張 complete take；TCP 完成回覆另交給假動作出口，證據只記錄一次請求。證據與 JSON 報告位於 `docs/evidence/ticket-16/`；這是本機 Quartz 受控畫面，未冒充 JetKVM frame，離線圖使用合成排序時間，不測來源 PTS。
- 本機未安裝 Pillow、Quartz、aiortc 等 GUI/KVM dependencies，因此本次沒有啟動 prototype Tk／JetKVM UI，也未執行實際 JetKVM、設備動作、部署或發布驗收。現有上位機 TCP 呼叫端仍未定位；維護／提供更新由使用者負責，當地 TE 協助設備部署，配對技術步驟見 `docs/TICKET16_DEPLOYMENT.md`。Ticket 12 AC 1 及 Ticket 13 AC 4 保持未通過。
- 審查修正 commits：`49e6378deccac2690c9118562d482dd7a80c4922`（來源 PTS／容量隔離與型別註記）、`421e2b084a56ca227b0a48bda569be5b2209e0b8`（假動作出口一次性證據）及回放報告 `17a09c106b32adeb531561e9b0148f575bb9487a`。固定兩 repo 基準 Standards／Spec 最終複審無未解發現；prototype 15 focused／32 全套測試與 5 張 App Quartz frame replay 通過。`17a09c1` 是第一輪完整實作／證據同步檢查點；最後摘要提交後，Gitea `origin` 與同一 GitHub repo 的 `codex/ticket-16` 再次核對至本摘要目前 commit。GitHub HTTPS push 因未配置帳號憑證失敗，透過同一 repo 的 SSH URL 推送成功，未更改 remote 設定。App 分支也已把最後驗收文件提交同步至 Gitea 與 GitHub。初次實作同步檢查點未合併，因 AC 1 的 maintainer 與部署安排當時尚未確認；使用者其後確認權責，已整理雙端配對部署步驟，依原授權重新檢查合併條件。實際 KVM／設備／發布驗收依使用者批准暫緩，仍保持未勾選；TCP state-only 格式相容性待確認。尚未授權以本機結果宣稱 Ticket 17／18 完成。

## 2026-10-05 權責補充

- 使用者負責程式維護與提供更新，當地 TE 工程師協助更新到設備。`docs/TICKET16_DEPLOYMENT.md` 記錄版本配對、隔離候選、暫停流程後同步更新及現場查核；具體部署日期／設備與 TE 執行紀錄仍待現場補充。
- AC 1 的非硬體責任者缺口已解除，實際 KVM 與現場部署並未因此驗收通過。已批准的實機延期及 TCP state-only 相容性待確認維持原狀。
- 本次僅更新文件，程式未變，沿用 32 tests 與原固定基準雙軸審查。合併／合併後驗證／同步／清理由實際結果另行記錄。

- 部署環境補充：ATE 設備只連接 SFC 網路，無其他對外網路，因此 Log App 更新由 TE 人工搬入；上位機實際執行 `B518_JetKVM_Log`，工廠內網是否可用尚未確認。自動更新只是未來可能性，未在 Ticket 16 新增。

## 契約 1.1 單排修正

- 後續 Spec 複審找到真實單排畫面拒判缺陷：原合成樣本錯畫第二排而掩蓋問題。先由 raw-frame／RoundFrameGate 公開 seam 紅燈重現，再同步 App／上位機升版 1.1，新增獨立排數 rail，按一／兩排取樣，舊 1.0 或遮擋／裁切第二排均拒判。
- 實際 Tk／Quartz 21 張新畫面涵蓋容量 1／4／6／10／11／12／20 與非恆等映射，磁碟 audit 與公開快照一致。`verify_ticket16_layout_frames.py` 從未分類像素重建七輪，一輪一次假動作請求；另用五張新 1.1 四狀態畫面重跑 `verify_ticket16_app_frames.py`，review pause 與確認後一次取用通過。證據位於 `docs/evidence/ticket-16/contract-1.1/`，非實際 JetKVM 或真實設備動作；舊 1.0 證據保留歷史用途。

- 1.1 修正後 `python3 -m unittest tests.test_round_frame_consumer -v`：18 tests；`python3 -m unittest discover -s tests -v`：35 tests，全數通過。App 完整 185 tests 通過。現行命令以 `contract-1.1` 的新畫面為輸入，舊 1.0 圖必須被新版拒判。型別檢查設定仍未配置。
