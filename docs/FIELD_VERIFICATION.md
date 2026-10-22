# 現場驗證清單

開發階段無法連實機驗證的項目，集中記錄在這裡；到現場後逐項執行，填入日期與結果。未驗證項目不得視為已完成。

欄位：**步驟**／**預期結果**／**驗證日期**／**結果（通過／失敗＋備註）**。

## 部署與設備（原 `DEPLOYMENT.md` 第 8 節、`LABVIEW_LOG_SOLUTION_INTEGRATION.md`）

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| D1 | 檢查 JetKVM 韌體的靜態 IP 設定選項（`DEPLOYMENT.md` 第 2 節） | 九台都能設定靜態 IP 並在重開機後保留 | | |
| D2 | 量測 FCT 推入前訊號到首次寫檔的時間（`DEPLOYMENT.md` 第 4 節） | 取得實測值，據此決定 Log Solution FCT start 期限 | | |
| D3 | 人員按停止監控，觀察 Log Solution header 標記（`PROTOCOL.md` 5.4、第 10 節） | 1.2：Complete 含 `STOPPED`，`check_finish` 交付 `taken`；1.1：Standby，回 `round_lost` | | |
| D4 | 混合真機的錄影重播壓測，九台同時運作（`LOAD_TEST.md`） | CPU、記憶體、回覆延遲在 `DEFAULT_LIMITS` 內 | | |
| D5 | LabVIEW 端 type definition 與 VI 改版後實際收發（#13，人工） | 依 `PROTOCOL.md` 與 `LABVIEW_HANDOFF.md` 的呼叫順序全部指令往返正常 | | |
| D6 | 實際 JetKVM 的壓縮與縮放下讀取 Log Solution 畫面 | 狀態標記、20 格結果與已開始通道判讀正確 | | |
| D7 | ATE 動作（SN 輸入、開始測試、Dock 點擊）實機操作 | 點擊位置正確、HID 無錯誤 | | |

## C3_2：KVM 斷線偵測（#58）

前提：#58 已部署；一台設備已用 Monitor UI 確認可快照。

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| K1 | 閒置時拔掉該 JetKVM 網路線，等 60 秒後插回；再送 `snapshot` | 拔線後 Core log 出現 `peer connection failed`／`影像軌道結束` 任一（拔線不會送 DTLS close，要等 ICE consent 逾時，推論約 24～36 秒，**結果欄記錄實際秒數**），之後心跳淘汰連線（淘汰紀錄）。等 60 秒仍未出現任何訊號，記為**未偵測**並回報；插回後 `snapshot` 自動重連成功 | | |
| K2 | `check_finish`（`timeout_sec` 120）長輪詢中拔網路線 | 死亡訊號出現前，畫面靜止會回 `frame_frozen`，這是預期行為（影格太舊不算斷線），可以重試。死亡訊號出現後，進行中的 `check_finish` 或下一個指令回 `kvm_connect_failed`。結果欄記錄拔線到第一次回 `kvm_connect_failed` 的秒數 | | |
| K3 | K2 之後插回網路線，重新 `monitor_start` → 開測 → `check_finish` | 新一輪正常 `taken`，不需要重啟 Core | | |
| K4 | 閒置時讓 JetKVM 重開機 | 連線被淘汰；JetKVM 開機完成後下一個指令自動重連成功 | | |
| K5 | 測試進行中讓 JetKVM 重開機；進行中的 `check_finish` 回 `kvm_connect_failed`，開機完成後再送 `check_finish`，且結果畫面已出現 | 重連後的那次回 `manual_verification_required`（斷線期間無法確認同輪） | | |
| K6 | 測試機畫面長時間靜止（例如 10 分鐘無變化）時送 `check_finish` | 不被誤判為斷線，不觸發重連 | | |
| K7 | 長輪詢中讓 JetKVM 正常重開機（會送 DTLS close，不是拔線） | 死亡訊號立即出現，進行中的指令立即回 `kvm_connect_failed`，不等 `frame_frozen`；開機後下一個指令自動重連 | | |

## 畫面凍結時的 HID 指令（#74）

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| F1 | 測試機 HDMI 停在監控中畫面（或 JetKVM 停止更新，PTS 不前進），送 `start_test` | 回 `frame_frozen`，DFU 未點 OK、BT 未點 Start All，`data.action` 為 `""` | | |
| F2 | 同 F1 狀態送 `input` 與 `monitor_start` | 回 `frame_frozen`，未送任何 HID；`monitor_start` 不建立本輪開始請求 | | |
| F3 | 送 `monitor_start`（`timeout_sec` 設 60），等待 Preparing 時中斷畫面串流 | 數秒內回 `frame_frozen`，不等到 60 秒 | | |
| F4 | JetKVM 畫面靜止 10 分鐘（測試機不動作）後送 `start_test`／`input` | 正常執行，不回 `frame_frozen`。若 JetKVM 靜止時降低送出影格數（間隔超過 1 秒），此項會失敗，結果欄記錄實際送出間隔 | | |

## KVM 重連後同輪重問（#56）

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| R1 | 取得 `taken` 後拔插 JetKVM 網路線，待重連後同輪再送 `check_finish` | 回 `already_taken`，結果與第一次相同 | | |

## 顯示契約 1.3 與心跳（#32、#77，部署前關卡）

橋接只支援顯示契約 1.3，須與 Log Solution 1.3 同時部署。

| # | 步驟 | 預期 | 結果 | 備註 |
| --- | --- | --- | --- | --- |
| L1 | 以實際 JetKVM 擷取 Log Solution 1.3 畫面，量測狀態與心跳每小格像素（LS7）。讀數取自 `core_status` 每台設備的 `frame_size`（影格寬×高）與 `cell_px`（標記小格像素，= `picture_scale` × 10）；Monitor UI 站別卡片同步顯示，須先讓 App 產生幾次讀取（check／inspect 或 monitor）再記錄 | 記錄每台的 `frame_size` 與 `cell_px`；確認 1 倍輸出下 10 px 格在壓縮後仍判讀正確，否則由 App 放大兩組標記後重測。`readable_ratio` 低於 1 時記下 `last_reading.reason` | | |
| L2 | 現場 ATE Mac 的 HDMI 輸出解析度與 macOS 縮放設定。與同一台設備的 `core_status` `frame_size` 對照：兩者不同（例如 HDMI 720p、縮放模式），即為影格被縮小 | 記錄實際值（HDMI 輸出、縮放設定、`frame_size`），與 L1 一致；不一致時標示為降解析度 | | |
| L3 | 正常運作下長輪詢 10 分鐘 | 不回 `log_solution_heartbeat_stopped` | | |
| L4 | 讓 Log Solution 主迴圈卡住（例如除錯中斷） | 約 2 秒（3 張有效觀察）內回 `log_solution_heartbeat_stopped`，不送 HID | | |
| L5 | 以其他視窗蓋住心跳格／整個 Log Solution | 判不可見並點 Dock，不誤報 heartbeat_stopped | | |
| L6 | 現場若仍有 1.1 App 而橋接已換 1.3 | 一律不可見，不誤判；確認部署 SOP 為兩者同時切換 | | |

## HMI 外觀與 Windows 縮放（#107）

前提：GitHub Actions 的 `Build Windows Executable` 最新一次執行為綠勾。

| # | 步驟 | 預期結果 | 驗證日期 | 結果 |
| --- | --- | --- | --- | --- |
| H1 | 依下方「H1 操作步驟」1～6 執行 | 每一項都與預期相符；任一項不符，結果欄寫明縮放值與項目，並附截圖 | 2026-10-22 | 部分通過（Win11 筆電 2880×1800）：100%／125%／150%／200% 文字清楚（Log `Windows DPI: shcore, tk scaling 2.667`＠200%）、emoji 無方框、系統深色下維持淺色、🌐 中英切換正常 ✔。版面問題 ✘：預設視窗太小致 Log 被擠成只剩標題、服務燈號被切在右緣、`▶ Live view` 字被截斷、設備設定視窗欄位與 Test 按鈕被切、勾選框在高 DPI 縮成「⌐」。待後續 issue 修正後重驗 |

### H1 操作步驟（Windows 11）

1. 用已登入、具讀取權限的 GitHub 帳號開專案頁，點上方「Actions」，點最新一筆綠勾的「Build Windows Executable」，在最下方「Artifacts」下載 `AtlasKVM-windows`，解壓縮到桌面。Artifact 保存 90 天；過期時到同一頁按「Run workflow」重新產生。
2. 開啟解壓縮後的資料夾，雙擊 `AtlasKVM.exe`。（不需要安裝，不需要 Python。）程式啟動後，點視窗下方的 Log 面板，第一批行中應有一行 `Windows DPI: shcore（或 user32）, tk scaling …`，把這行抄進結果欄。
3. 在「設定 → 系統 → 顯示 → 縮放」依序設為 **100%**、**125%**、**150%**。每改一次縮放，就先關閉 AtlasKVM 再重新開啟（縮放只在程式啟動時套用）。
4. 每個縮放值下確認：
   - 文字與按鈕清楚、不模糊；視窗內容沒有被切掉或重疊。
   - 按鈕上的圖示（例如 🩺、⚙）顯示為圖形，**不是方框 □**。
5. 到「設定 → 個人化 → 色彩 → 選擇您的模式」改成「深色」，AtlasKVM 仍維持淺色背景；看完再改回原本設定。
6. 按視窗上的 🌐 切換語言：中文 ↔ 英文都能切，文字沒有被截斷。切完後關閉程式，再開啟，語言設定會保留。

**回報方式**：在結果欄寫「100% ✔ / 125% ✔ / 150% ✘（右上按鈕文字被截斷）」這類格式；失敗項目附截圖與 Windows 版本（設定 → 系統 → 關於）。
