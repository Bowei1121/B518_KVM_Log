# B518 JetKVM — 專案摘要（2026-10-19）

日期：2026-10-19（Asia/Taipei）
前一次詳細紀錄：`docs/PROJECT_SUMMARY_20261017.md`。

## 2026-10-17 結果

- grill-with-docs「Log Solution 被遮住時的可見性恢復」定案並實作：排除法判定「Log Solution 不可見」、只有可靠畫面歸零、30 秒觸發點 Dock、同一 `check` 最多 3 次、找不到 Dock 圖示回 `log_solution_not_found`、點 Dock 後滑鼠移到畫面頂端中央。
- 票 #31 由 PR #33 合併 main（`53bfc55`），全套 454 passed；#32（支援 1.2 左定位方塊新位置）為 `needs-info`，等 Log Solution 依 C6 定座標。
- `docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md` 新增 C6：拿掉「KVM RESULT」字樣、左定位方塊移到色帶第 1 格正上方、五條限制條件、先更新橋接再更新 Log Solution。
- 主資料夾 checkout 由 `49dfea6` 快轉到最新 main；`AGENTS.md` 新增「回覆語言請使用繁體中文」（`14df02d`），兩個 remote 同步。
- 已提供 #32 的 Haiku／implement-spec prompt（含開工前檢查：標籤須為 `ready-for-agent`、Log Solution 契約須已寫入新座標）。

## Log Solution 端的回應（唯讀查證 Log Solution repo `fa2b571`）

- Log Solution 於 2026-10-17 讀過 1.2 建議並排序：P0 C1＋C2、P1 C4／C3／C6＋C5。尚未核可實作；契約文件仍為 1.1（左定位方塊仍在 x=82）。
- 其分析指出：C1 須同步處理稽核重建；C6 第一格在 x=0，須驗證定位點左緣與外側留白；移動定位點不能涵蓋所有只遮色帶的形狀，consumer 須保留格位可讀性驗證（與本 repo 排除法一致）。

## 本日

- 使用者要求閱讀 Log Solution `docs/refactoring/KVM_DISPLAY_CONTRACT.md`（1.1），評估其狀態辨識格的意義能否結合到橋接判讀流程。

## 契約 1.1 與橋接判讀對照

- 幾何（左右定位方塊、排數標記、狀態標記、結果格）、黑白門檻（0–80／176–255）、四種狀態圖樣，橋接 `round_frame_consumer.py` 與契約 1.1 完全一致。
- 狀態意義對應：Standby → 本輪未見監控中為等待、已見監控中為 `round_lost`；Monitoring → 記為已開始、等待；Review → 立即 `paused`；Complete → 兩張相同完整畫面且格數 = 通道數才 `taken`。契約要求接收端自行驗證前後一致與遮擋，由兩張畫面一致與 #31 排除法涵蓋。
- 缺口：契約沒有格子顏色表；Log Solution `STATUS_COLOURS` 中 `STALLED` 與 `TIMEOUT` 同為 `#ff9900`，且 `STALLED` 未被使用；NOTEST 兩邊差一個色階；契約「App 與上位機必須一起更新」與 A12 過渡期雙支援衝突。

## 決定

- Q1：1.2 建議文件新增 **C7**：格子顏色表寫進契約、移除未使用的 `STALLED`、NOTEST 以 `#f04bf1` 為準、容許誤差為 RGB 歐氏距離 ≤ 20（任兩色須 > 40；WAITING 與 STOPPED 約 45，餘裕小）。
- Q2：橋接釘住顏色並開票 **#34**（`ready-for-agent`，#21 的 sub-issue；本機副本 `.scratch/log-solution-all-stations/issues/09-pin-status-colours.md`）。
- Q3：C5 改為「1.2 起上位機先支援新舊兩種畫面，再更新 App」。

## 第五種圖樣 0011 的整合

- 使用者決定以 `docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md` 的第五種圖樣 MANUAL_STOP_COMPLETE（0011）為準，取代 1.2 建議原 C1「人工停止沿用 Complete」。
- 主資料夾 main 上未推送的 3 個 commit（`b92f25e`、`f09f2d4`、`243b64f`）已 rebase 到 `fd6a8ae` 之上，放在分支 `docs/handoff-1-2-0011`；兩份 10-19 摘要合併為本檔，主資料夾那份完整保留在下方一節。
- 1.2 建議 C1 改寫為第五種圖樣（以 HANDOFF 為準）；相容性表、驗收清單、PROTOCOL.md 5.4 與第 10 節同步更新。
- 開橋接票 **#36**（已改為 `ready-for-agent`，#21 的 sub-issue；本機副本 `.scratch/log-solution-all-stations/issues/10-manual-stop-pattern-0011.md`）。已確認的協定：交付時 `state = "manual_stop_complete"`（不新增欄位）；未見本次 Monitoring 回 `error = "manual_verification_required"`。
- 主資料夾的本機 main 仍是 rebase 前的 3 個 commit，PR 合併後需要把它對齊到 `origin/main`。

## 主資料夾工作階段的 10-19 紀錄（HANDOFF 與第五種圖樣）


### 本次接續與範圍

本輪由 Log Solution 討論工作交接文件到 KVM_Log；未變更橋接產品、指令或 GitHub 議題。先讀 AGENTS.md、CONTEXT.md 及相關 ADR，未把提案或歷史測試當成已部署結果。

既有提案為 LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md（2026-10-17 補充 C6）。原始碼查核：consumer 仍為 1.1、四個狀態；能解析 STOPPED 格位，但人工停止的 App 標記落 STANDBY，不能以此可靠交付整輪人工停止原因。

### 已確認的討論結果

- READY 可接受新輪，BUSY 表示本輪工作／待確認；監控就緒要等來源基準完成，上位機再啟動 ATE。
- 藍框只表示一排／兩排；紅框採五種狀態語意，區分正常與人工停止可交付。新增的第五圖樣仍為建議，不是已核准位元規格。
- 通道及色帶保留 STOPPED，已取得結果保留，操作員決定重工範圍；整輪人工停止原因須明確傳遞。
- TESTING 是通道觀察，不是完成結果的必經條件；FCT start 先採120秒，整輪無活動也無有效結果時停止、NOTEST、REVIEW，人員確認後才交付。
- 視窗事件提升，不永久置頂／不強制焦點；幾何沿用提案方向，精確座標待驗。

### 產物與未定事項

新增 [跨專案狀態辨識交接](LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md)，與 Log Solution 主文件為相同內容快照；同步 CONTEXT.md 的已確認詞彙。

第五圖樣、精確幾何、準備期間畫面、未見 BUSY 就中止／逾時的同輪性，以及人工停止／TESTING 的對外協定格式仍須確認。未修改原提案、已發布1.1或產品程式；本輪只做文件一致性驗證，未跑產品測試。


### 第五種圖樣確認（2026-10-19，後續更新）

使用者明確同意 MANUAL_STOP_COMPLETE 採上排白白、下排黑黑；左上／右上／左下／右下依序為 0011（1黑、0白）。兩專案交接文件已同步改為已確認，取代前文的圖樣待確認狀態。精確幾何、準備期間畫面、未見 BUSY 就停止／逾時的同輪交付及 TCP 回覆格式仍未定稿；本次只更新文件，未修改或驗收產品程式。


### 未見本次 BUSY 就人工停止（2026-10-19，後續確認）

使用者同意：Log Solution 立即停止並呈現完整格位結果（既有終局保留，未完成為STOPPED）；無待確認後顯示人工停止可交付圖樣。但橋接若未見本次BUSY，不能證明畫面屬於這次開始，須向上位機回報「需人工核對」、不自動交付、不自動重測。此確認只涵蓋人工停止；未見BUSY的準備逾時及人工核對後恢復／核准程序、協定欄位仍待定稿。兩專案文件相同內容已同步；未修改產品程式。


### 未見本次 BUSY 就來源準備逾時（2026-10-19，後續確認）

使用者同意：來源準備卡住、逾時且橋接尚未見本次BUSY時，Log先顯示逾時警報／REVIEW；即使人員確認警報，橋接仍回「需人工核對」，不自動交付、不自動重測。這取代前文此情況待確認的狀態；警報確認不能代替同輪證據。人工核對後恢復／核准、準備期間畫面、精確幾何及對外協定欄位仍待定稿。本次只更新兩專案相同交接文件與摘要，未修改產品程式。


## 準備中與人工核對後取消（2026-10-19，後續確認）

使用者同意增加第六種 PREPARING 語意：HMI 為 BUSY／準備中，橋接須等待後續 MONITORING 才允許 ATE 開測。本次文件配套配置為0101（白黑／白黑），既有四圖樣及已確認人工停止0011保持；六碼均兩黑兩白。先前「未見本次BUSY」的同輪保護，從本次起精確指「未見本次MONITORING監控就緒」，不能以PREPARING取代。

使用者同意需人工核對的中止／逾時路徑預設取消本輪，保留原結果及紀錄，由操作員決定重工範圍；排除問題後明確開新輪，不強制交付不確定結果、不自動重測。正常可靠已保存結果的重傳政策不變。兩專案交接文件與詞彙同步更新；精確幾何、協定欄位及配對實作驗收尚待完成，未修改產品。


## 啟動失敗異常格、人工復位與期限先後（2026-10-19，後續確認）

使用者同意啟動前配置／路徑失敗及接受後來源準備失敗均BUSY／待確認；沿用REVIEW1010作異常訊號，橋接通知上位機報警並暫停。前者不造新輪／不錯掛上一輪，後者保留失敗輪次；不產生假產品FAIL或人工停止完成。人員排除問題、確認後復位：結束失敗請求或取消失敗輪次，保留紀錄，才回READY；下一次明確觸發才開新輪，不自動重試。

使用者同意維持到期先判deadline再讀來源；若期限前尚未接受可信證據，不以該次後讀資料或來源自報早期時間挽回到期狀態。這取代前文故障流程及期限臨界點待定事項；兩專案文件與詞彙相同規則已同步，產品尚未修改／驗收。


## check_start／check_finish 確認（2026-10-19，後續更新）

使用者同意用monitor_start確認Log監控就緒、check_start確認ATE可信測試活動、check_finish確認完整結果；check_start不能只看MONITORING或HID成功。TESTING／COMPLETING或本輪有效PASS／FAIL等結果可為活動證據；回報實際通道及證據類型，不推論全通道開始。若已取得完整可交付結果，可直接交付，不再強迫等待check_finish；NOTEST／TIMEOUT／STOPPED不當開測證據。每請求只回一次，活動回覆不消耗最終結果。

唯讀查核KVM_Log PROTOCOL.md已文件化人工停止state=manual_stop_complete、缺監控就緒證據回manual_verification_required，程式仍待實作；交接文件同步引用此方向，未改寫現行TCP1.0。新命令完整欄位／版本、舊check遷移、精確幾何及配對實作驗收仍待完成。本次只更新兩專案交接文件、詞彙及摘要。


## Log Solution 正式規格與驗收責任交接

2026-10-19 使用者確認Log Solution只驗收本程式能驗證的輪次／保存／真正App輸出；KVM相關由本專案自行驗收、有問題再發需求，不作為Log Solution完成門檻。兩邊狀態辨識交接快照已加入此責任說明，先橋接後App的部署順序不變。Log Solution父規格已發布至Bowei1121/B518_205_207_ATE #77（ready-for-agent），採88故事／25實作／17測試決策；本次未修改本專案程式或現行TCP規格。

## grill-with-docs：橋接協定修訂（Q1–Q19，KVM_Log 本程式範圍）

- Q1：移除 `check`，新增 `check_start`／`check_finish`；協定版本維持 1.0（未上線），不留別名；`inspect` 保留唯讀。
- Q2／Q14：兩指令共用 `data`：`kind`（`started`／`taken`／`already_taken`／`waiting`／`paused`／`unknown`）、`state`（含 `preparing`、`manual_stop_complete`）、`reason`、`capacity`、`active[{slot, evidence: testing|result}]`、`results[]`。NOTEST／TIMEOUT／STOPPED／WAITING 不算活動。
- Q3／Q11：`check_start` 以連續兩張新鮮影格判定活動（序號前進、間隔 ≤ 1 秒），通道取兩張交集，中間出現 unknown 重新計算；已見完整可交付結果時直接回 `taken`。
- Q4：兩指令必填 `timeout_sec`（0～3600），與 Log 測試期限無關；可見性恢復兩指令皆套用。
- Q5：本輪開始請求：送過 `monitor_start` 但未見本次 Monitoring 而出現可交付畫面 → `manual_verification_required`；從未送 `monitor_start` → `monitor_not_ready`；交付或回 `manual_verification_required` 後清除。
- Q6：`monitor_start` 新增必填 `timeout_sec`（上限 300），PREPARING 期間持續等待；一開始即 PREPARING 時不再送快捷鍵、直接等待。
- Q7：`monitor_start` 遇 REVIEW（開始前或送出後）立即回新錯誤碼 `log_solution_review`；`check_*` 遇 REVIEW 仍回 `paused`。
- Q8：PREPARING 時 `input` → `log_solution_busy`、`start_test` → `monitor_not_ready`、`check_*` → `waiting`（可靠畫面，歸零可見性計時）、`inspect` 回報 `preparing`。
- Q9：#36 併入，改由 spec #3 取代。Q10：輪次取消與新輪次 ID 不做（協定 1.1）；只針對本程式。
- Q12／Q13：`check_start` 保留可用但非必經；可重複呼叫，回當下最新 `active`，不消耗結果。
- Q15：`inspect` 改用同一格式（不會出現 `taken`）。
- Q16：寫成 spec #3，範圍只限本 repo；不含 #32、#34、協定 1.1、Log Solution 修改。測試切入點沿用 spec #2，合成畫面加 PREPARING、0011、TESTING／COMPLETING。
- Q17：`already_taken` 帶與第一次相同的完整結果，可重複查看；同輪判斷為完成種類＋格數＋各格結果相同且未開始新輪；只有 `taken` 可觸發設備動作。橋接重啟後重傳屬協定 1.1。
- Q18：六種 `kind` 定義與 LabVIEW 動作表定案。Q19：新增 `docs/LABVIEW_HANDOFF.md` 給 LabVIEW 設計者（呼叫順序、kind 表、錯誤碼處置、何時可做設備動作）。
- CONTEXT.md：「同輪重傳」擴充為含上位機主動重看；新增「本輪開始請求」。
- to-spec：spec #3 發布為 **#40**（`ready-for-agent`），本機副本 `docs/specs/0003-bridge-protocol-start-finish.md`；測試切入點沿用 spec #2（Q16 已確認）。新增 `docs/LABVIEW_HANDOFF.md`。#36 以「由 #40 取代」關閉；#13 已留言提醒依 #40 與交接文件設計。
- to-tickets：spec #40 拆成 7 張票 **#41～#47**（#40 的 sub-issue，原生 blocked-by：#42←#41、#43←#41、#44←#42／#43、#45←#44、#46←#45、#47←#42／#44／#45），皆 `ready-for-agent`；本機副本 `.scratch/bridge-protocol-start-finish/issues/01～07-*.md`（本機編號 = GitHub 編號 − 40）。1～5 都改 TCP 伺服器，需依序進行。
- 實作前釐清（Grok 提問，2026-10-19 決定）：(1) `monitor_start` 開始時已是準備中也建立本輪開始請求，`monitor_not_started` 不清除請求；(2) `check_*` 判斷順序：REVIEW → `paused`；同簽章且無新一輪信號 → `already_taken`（排在請求檢查之前）；不可靠 → `unknown`；無請求 → `monitor_not_ready`；其餘照本輪開始請求規則。交付後簽章改變且無新一輪信號 → `monitor_not_ready`；(3) 人工停止完成同一般完成：已交付或已回 `manual_verification_required` 後 DFU `input` 可輸入下一輪，未交付時 `log_solution_busy`。已更新 spec #40（issue 與本機副本）、#42／#43／#44 驗收條件與本機副本、`docs/LABVIEW_HANDOFF.md`。
- PR #50 重審（`2b1ace7`）：A1～A4、B1～B4、C 已處理並於本機確認；已留言請 Cursor 補回 fail-closed 的 `internal_error` 測試。UI 取消測試不穩定為 main 既有 bug（30 次失敗 8 次），開票 #51（`bug`、`ready-for-agent`），本機副本 `.scratch/core-ui-stability/issues/04-ui-cancel-race.md`。
