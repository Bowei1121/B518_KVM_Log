# B518 JetKVM — 專案摘要（2026-10-14）

日期：2026-10-14（Asia/Taipei）

## 既有討論與成果

本節根據版本控制中的專案摘要、CONTEXT.md、ADR 與 Git history 重建；未讀取不可見的歷史聊天全文。歷史細節見 `docs/PROJECT_SUMMARY.md` 與 `docs/PROJECT_SUMMARY_20260901.md`～`20260903.md`。

- 本 repo 負責 HDMI 串流、視覺結果判讀與 HID 操作；測試機本地 Log 的讀取與解析由另一個獨立專案負責，不建立跨專案程式依賴。
- Ticket 16 已合併顯示契約 1.1：DFU 直接判讀原始 BGR frame、1–20 格結果、明確一／兩排標記；先見 Monitoring，兩张新鮮完成 frame 後同輪一次性取用。DFU 輸入 profile 的 4／7 格與結果容量不同。
- 已有 saved-pixel replay 與假動作出口證據；實際 JetKVM、設備、發布 App、外部 TCP status-only 相容性仍待驗。使用者維護與提供更新，當地 TE 人工配對部署；詳見 `docs/TICKET16_DEPLOYMENT.md`。
- 2026-10-06～07 Ticket 01–07 已合併：Headless JSON Protocol、Device Lock fail-fast、KVM Connection Pool heartbeat/idle 淘汰、Frame Freeze Guard、Long Polling、多工連線、Core Service 程序管理、獨立 Monitor UI。
- 架構評估基準：`main` / `7c3faba`。ADR-0001～0006 為 Accepted，保留其決策；`third_party/jetkvm` 為固定版本 submodule。

## 本次使用者決定與技能設置

- 使用者要求先運行 setup-matt-pocock-skills，再運行 improve-codebase-architecture。
- Issue tracker：GitHub Issues，`Bowei1121/B518_KVM_Log`。因 `origin` 指向公司 Gitea，`gh` 命令必須明確指定 `--repo`。
- Triage labels：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`；只記錄詞彙，本次未建立遠端標籤。
- Domain docs：single-context，沿用根目錄 `CONTEXT.md` 與 `docs/adr/`。
- 使用者已確認完整草稿，選擇建立 `AGENTS.md`。設定文件為 `docs/agents/issue-tracker.md`、`domain.md`、`triage-labels.md`。
- `.scratch/jetkvm-relay-refactor/issues/` 保留歷史票據，未遷移或發布；本地票號與 GitHub 號碼不能直接混用。
- 使用者持續規則：程式修改完成後自動 commit/push；換日首次對話保存專案摘要。

## 架構評估結果

先讀 CONTEXT.md 與 6 份 ADR，依近 40 筆 commits 找 hotspots，並由一個子代理唯讀探索與執行不連硬體的 fake probes。候選未被實作，亦未定義新 interface。

1. **Strong：統一設備識別與 Device Lock（首選）**。`tcp_server.py` defaults 的 DFU/FCT/BT 都是 device=1；鎖只按 device，guard 用 station:device，pool 按 IP，snapshot 用 station OR device 匹配。fake probes 重現跨站 BUSY，以及 FCT:1 無 IP 時抓到 DFU .70。集中 identity module 可增加 locality、leverage 與 depth；保留 ADR-0002/0006。
2. **Strong：收攏各站別視覺判定**。TCP module 同時處理 station 判定與 fallback。fake probe 重現 BT 未命中 target_pattern=FAIL、無 timeout 仍回 PASS；DFU 非長輪詢也有 waiting/paused → PASS。需要集中視覺判定的 seam，測真實影格 adapter，不只測 fake attributes。未宣稱現場已發生誤判。
3. **Worth exploring：Frame Freeze Guard 串流知識**。guard 未追 stream_id，RoundFrameGate 已追。fake probe 重現舊 PTS=100、新 stream PTS=1 被舊狀態立即判 frozen；需區分同串流倒退與重連，仍保留 ADR-0005 防呆。
4. **Worth exploring：KVM Connection Pool 使用期**。pool 只更新取得當下 last_used；fake probe 重現 caller 仍持有 client 時 idle prune 將它 close。預設 600 秒，長 check 的現場風險未量測；需深化現有生命週期 module，保留 heartbeat/idle 淘汰。
5. **Worth exploring：Monitor UI 通訊排程**。status、snapshot、manual check 共用 socket lock，長診斷會讓其他 worker 等待；定時建立 status threads 可能累積。這是源碼時序推論，未測 GUI；需集中排程、取消、通道與 request 配對的 implementation，保留程序解耦。

其他觀察：預設 handler 未註冊 input/button；README 仍含舊逗號協定示例，與 ADR-0003 JSON Protocol 不一致。後續應核對文件與部署版本。

HTML 架構報告依技能規則保存在 OS temp，未加入 repo：
`/private/var/folders/gk/zpblb4q57bl24pt55p1m933h0000gn/T/architecture-review-20261014-093033.html`

臨時檔可能被系統清理；本摘要保留核心證據、候選與下一步。使用者選定候選後才進入 grilling；新的領域命名或決策依 domain-modeling 更新 glossary/ADR。

## 驗證與實際範圍

- `.venv` Python 3.12.13，既有全套 `.venv/bin/python -m pytest -q`：**116 passed, 30 subtests passed in 22.86s**。
- 初次受限沙箱禁止 localhost bind；以本機權限執行後通過，沒有把沙箱錯誤當成程式缺陷。受限測試程序已停止。
- 本次只新增代理設定與摘要文件，程式未重構；文件路徑、label mapping 與 Git diff 另以靜態檢查核對。
- 未連 JetKVM、未操作 ATE/HID、未啟動 GUI、未部署；既有測試通過不等於候選中的未覆蓋情況已驗收。
- 本次 commit 與兩個 remote 的同步狀態以 Git 查閱，避免在文件中記錄自身 commit 的循環引用。

- HTML 已完成結構靜態檢查；系統沒有可用的 HTML 預設開啟程式，內建瀏覽器禁止 file://，因此未完成 CDN 圖表與版面視覺預覽。

## grill-with-docs：已確認分工與結果來源

- 使用者已確認 Q1：LabVIEW 控制輸入 SN、啟動測試、等待結果與後續設備動作；橋接程式依指令透過 JetKVM 操作並回報畫面結果；Log Solution 讀取 ATE Log、管理輪次與人工確認、顯示可取用結果。
- 使用者已確認 Q2：採 A 方案，DFU／FCT／BT 所有工站均運行 Log Solution，正式結果統一由該人機畫面提供。
- 以上是已確認的目標分工，尚未修改程式；現行 JSON 判讀只有 DFU 使用此畫面契約，FCT／BT 仍使用原有模板，不能將目標當成既有能力。
- 領域定義已記入 CONTEXT.md。啟動輪次、參與位置、結果釋出、人工確認、逾時及重連規則仍待訪談；完整共同理解尚未確認，沒有開始實作。
- 前一回合配對查證：契約 1.1 幾何及四種狀態 pattern 對齊；NOTEST 顏色一個色階差異落在既有容差內。既有 App 真實 Tk／Quartz 圖片的五狀態與七種容量回放通過；橋接 raw-frame 18 tests／30 subtests、App 契約 7 tests 通過。
- 保存的 App 圖片經 current JSON dispatcher 與合成 sequence/PTS，可得到 `kind=taken` 及 20 格結果；同時重現無 timeout 的 Monitoring 請求回 `result=PASS, kind=waiting, results=[]`。本次是離線／記憶體驗證，未連實機。

## grill-with-docs：已確認輪次開始與待確認處理

- Q3：使用者同意「輸入 SN → 啟動 Log Solution 監控 → 確認監控中 → 啟動 ATE 測試 → 等待結果」。這是目標順序；舊 DFU input 的現有順序是 ATE OK 後送 Command+Shift+M，尚未修改。
- Q4：LabVIEW 知道本輪哪些位置有產品，以及各產品 SN；本輪參與清單可由此提供，不以 NOTEST 推斷合法空格。
- Q5：先採 A 方案，遇到待確認即通知 LabVIEW 暫停；人員在 Log Solution 處理後再繼續取結果。現行 JSON 長輪詢尚未提供立即待確認通知。
- 新確認詞彙已即時記入 CONTEXT.md。結果位置對應、已投入產品的 NOTEST／TIMEOUT 政策、斷線／重試與結果交付確認仍待釐清；沒有開始程式實作。
