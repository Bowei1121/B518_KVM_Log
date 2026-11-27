# Spec: Monitor UI 改用 C# WPF 第一階段（status 畫面）

GitHub issue：https://github.com/Bowei1121/B518_KVM_Log/issues/221（追蹤票）。

子票：[#224](https://github.com/Bowei1121/B518_KVM_Log/issues/224)、[#225](https://github.com/Bowei1121/B518_KVM_Log/issues/225)、[#226](https://github.com/Bowei1121/B518_KVM_Log/issues/226)、[#227](https://github.com/Bowei1121/B518_KVM_Log/issues/227)、[#228](https://github.com/Bowei1121/B518_KVM_Log/issues/228)。

相關文件：[#129 決定 B](https://github.com/Bowei1121/B518_KVM_Log/issues/129)（Core 中斷顯示的歷史來源，已由 user 2026-11-27 20:28 決定取代）、`CONTEXT.md`、`docs/PROTOCOL.md`（§1～3、§5.1、§5.9、§6～7）、`docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md`（含後續顯示契約修訂）、ADR-0002（device lock／fail-fast）、ADR-0004（Core 與 UI 分離）、ADR-0005（Frame Freeze Guard）、ADR-0008（手動 HID handoff）、ADR-0009（C# WPF 與 Python Core 邊界）、`docs/specs/0004-kvm-connection-command-timing.md`（格式參考）。

決策依據：ADR-0009 已於 2026-11-23 Accepted，使用者決定現在開始、先看對照圖再選方案；2026-11-25 11:00 Asia/Taipei，經國務卿 → 專案夥伴轉述原話：「選 B：WPF 第一階段」。本 spec 定義 B 案範圍；歷史追蹤票的 Proposed 與啟動前提描述不代表目前仍未選案。版本、部署等採 user 2026-11-27 的決定；仍未批准的並存操作與視覺選項依文末決定清單標示待決。

## Problem Statement

現有 Monitor UI 為 Tkinter TCP client。現場需要開始建立 Windows 原生介面的替換路徑，同時保留已驗證的 Python Core、LabVIEW 通訊及設備安全規則。若直接移植全部 Monitor UI，會把 stream、inspect、HID 操控與部署決策一起帶入，無法清楚驗收第一階段。

本階段必須讓操作員與現場工程師照 Core 清單（實機 8 台：DFU-01～06、FCT-01、BT-01）看設備狀態、辨別 Core 不可用與 KVM 異常，且不把上次資料誤當即時狀態。WPF 的輪詢本身不觀察影格，不能宣稱已檢查畫面活性或修復設備。

## Solution

建立獨立的 C# WPF status 用戶端，僅透過 status channel 的 `ping`／`core_status` 連線既有 Python Core。工作包為 .NET 專案（含連線設定與用戶端記錄）、TCP 連線與重連、設備 status 畫面、Windows CI 與打包、實機測試。

一次 `core_status` 取得完整設備清單與狀態，不為每台設備各開一條 status socket。畫面呈現 Core 連線狀態、設備狀態、異常警示、背景重連進度與讀取統計；不提供畫面預覽或設備操作。範圍以本 spec 為準；PROTOCOL.md 相容、顯示契約、Core 留在 Python 這些硬邊界，以 ADR-0009 與 PROTOCOL.md 為準。後續開發票以本 spec 為範圍與驗收依據，未決選項須先取得使用者決定，不能由實作者默認填入。

## User Stories

1. 身為操作員，我希望在同一個 status 畫面看設備，以統一設備 ID 與站別辨識各列，不必用 IP 猜測設備。
2. 身為現場工程師，我希望連上 Core 後取得設備清單與目前狀態，以便與既有 Python Core 的配置一致，不另維護一份設備來源。
3. 身為操作員，我希望辨別 WPF 到 Core 的連線、KVM 的連線及設備異常，以便知道是哪一層不可用。
4. 身為操作員，我希望 Core 斷線後用戶端能重連，且不凍結畫面；Core 恢復後以新回覆更新狀態。
5. 身為操作員，我希望 Core 從未連上時明確顯示不可用，不把空白資料當作設備正常，並提供連線位址及可理解的錯誤提示。
6. 身為操作員，我希望異常依既有優先序呈現，警示摘要、設備列與 tooltip 一致，不因設備正在執行指令就蓋掉畫面凍結或心跳異常。
7. 身為操作員，我希望看到 Core 回報的 KVM 重試倒數、重試中與失敗次數，能區別恢復中與連線失敗異常。
8. 身為操作員，我希望 Core 不可用時所有列（含原紅列）改為灰底「未連線」、KVM 連線燈 N/A，不保留 Last status；未取得過的設備資料不得捏造（user 2026-11-27 20:28，取代 #129 決定 B）。
9. 身為 LabVIEW 測試流程控制者，我希望 WPF 開啟、關閉、崩潰或重連都不影響 Core 與測試流程，也不送出設備操作或重啟 Core。
10. 身為現場工程師，我希望 status 用戶端保留顯示契約、Frame Freeze Guard、device lock 與 HID handoff 的邊界，不因 UI 改寫而繞過安全防護。
11. 身為維護者，我希望 Windows CI 可重現建置、測試及產出套件，以便在 Windows 11 驗證已定的 self-contained ZIP 交付。
12. 身為現場工程師，我希望用真實 Windows 與 KVM 環境確認 8 台狀態、Core 重啟、斷線恢復及高 DPI 顯示，實機依已定的 8 台清單驗證；實機測試站名：待使用者補。

## Implementation Decisions

- **Core 邊界（硬限制）**：Core 留在 Python；本階段不改寫 Core、不把 Core 搬到 .NET。WebRTC、KVM Connection Pool、辨識流水線與自動測試流程仍由 Core 負責。依 ADR-0004，WPF 與 Core 的行程、生命週期獨立；關閉 WPF 只清理自身資源，不送 `shutdown`。
- **協定相容（硬限制）**：`docs/PROTOCOL.md` 保持相容，本階段不做協定變更，不新增／改名指令或欄位，不改 envelope、錯誤碼、設備識別、長輪詢、結果與輪次語意。
- **顯示契約（硬限制）**：Log Solution 顯示契約不動；列數標記（排數格）、狀態標記、錨點／定位點、結果色帶與判讀幾何不得更改、不得蓋住、不得要求契約變更。不因交接文件檔名含 1.2 就退回舊幾何；遵守現行文件的後續修訂。
- **安全規則（硬限制）**：Frame Freeze Guard、device lock（ADR-0002 fail-fast）與 HID handoff 必須保留，不得破壞或繞過。status 用戶端只走 `ping`／`core_status`，不得送會影響凍結、鎖或 HID 工作階段的指令，亦不得直接連 KVM。既有手動操作用戶端的交接規則仍由 Core 維持。
- **協定語言（硬限制）**：Core／TCP 協定字串僅限 English ASCII，包含 `message` 與錯誤文字；UI 翻譯與本機記錄屬不同層，不把中文送入協定。
- **status channel**：此處 status 是 client 的連線分工，使用 `ping`／`core_status`，不是 LabVIEW 的 `status` 指令。沿用現行 Tk，定時輪詢只用 `core_status`，`ping` 只用在測試連線。兩個請求的 `device` 均為 `""`，帶現行共同欄位；`req_id` 為 1–64 字元、每筆請求不重複；同一 socket 一次只送一筆，收到完整回覆後才送下一筆。遵守 UTF-8 無 BOM、LF 分包與 `protocol_version="1.0"`；TCP 分段或一次收到多行時須正確組包、分行，不能把一次讀取等同一筆 JSON。
- **本機連線**：WPF 與 Core 在同一台上位機；Core 只監聽 `127.0.0.1`，預設位址 `127.0.0.1:5000`。使用者填入非本機位址時須提示 Core 僅接受本機連線，不據此擴充連線安全政策。
- **回覆與錯誤**：核對 envelope 的 `req_id`、`device`、`cmd` 與版本，再依 `status`／`error` 判斷；不能解析 `message` 決定狀態。逾時、取消、錯誤回覆、格式錯誤或不匹配回覆不能覆蓋為成功狀態；失效連線須清理，避免晚到回覆套用至新請求。背景輪詢與重連不得阻塞 WPF UI 或造成重疊請求。
- **nullable 相容**：依 §5.9 為 `retry_in_sec`、`first_fail_at`、`last_connect_error` 及讀取統計等欄位保留 nullable 語意。這是相對 §1「不使用 null」通則的既有例外，須以現行 Core 回覆驗證；不得為了 DTO 方便而改 Core、改協定或把未知值當成正常值。
- **設備與重連責任**：照 Core 清單（實機 8 台：DFU-01～06、FCT-01、BT-01），以統一設備 ID 為準、依清單筆數呈現；8 台是實機目標，不是固定列數，非 8 台時亦須可用（含捲動），空清單不補造設備（user 2026-11-27）。WPF 只重連到 Core；KVM 連線重連、失敗計次與狀態解除由 Core 負責。重試次數門檻與倒數使用 §5.9 現有欄位，不由 WPF 另算失敗門檻或發動 KVM 重連。
- **欄位對應決策表**：以下三欄是 `tcp_server.py` 已回覆的 Core 既有欄位，本階段引用它們不算協定變更。Core 計算 `stations[].status` 的優先序為 BUSY > FROZEN，故不得由 `status` 反推凍結。

| 顯示語意 | 唯一判讀欄位 | 規則 |
| --- | --- | --- |
| 畫面凍結 | `stations[].is_frozen` | true 即 Frame frozen，包含 `is_busy=true` 同時 `is_frozen=true` |
| 執行中 | `stations[].is_busy` | 不得把 Core BUSY 當 Log BUSY／監控就緒 |
| KVM 連線 | `stations[].is_connected` | 獨立燈號與 tooltip；不由 `status` 推斷 Online／Offline |

- **狀態優先序決策表**：僅在取得 Core 有效即時資料時套用；Core 斷線優先依 Core 斷線顯示決策表處理。沿用 `host-app/station_presentation.py` 的現行選擇規則，依下表由上往下取第一個命中作為一列唯一主狀態（Tk 為 if/elif）。依欄位對應決策表明定的欄位來源，凍結與執行中使用布林欄位；其餘選擇順序沿用 Tk，`status` 僅供 IDLE／DISCONNECTED 等既有狀態 fallback。每列 tooltip 固定列出設備 ID、所有成立的凍結／兩種心跳異常、實際 KVM 連線與執行中資訊；未連線時另列連線失敗及重試欄位，因此次要異常不遺失。警示摘要與列／tooltip 一致，不以低優先狀態覆蓋主狀態。色彩與閃爍細節仍待問題 7，user 2026-11-27 20:28 已決定的 Core 中斷行為除外。

| 依序命中條件（前列皆未命中） | 主狀態 | 被蓋掉資訊的位置／處理 |
| --- | --- | --- |
| `is_frozen=true`，其餘任意（含 busy、斷線、兩種心跳） | Frame frozen | tooltip 列出次要心跳、busy、實際連線；未連線的失敗／重試也列出 |
| `is_heartbeat_stopped=true`，含同時 not found | Heartbeat stopped | tooltip 保留 Heartbeat not found、busy、連線及未連線的失敗／重試 |
| `is_heartbeat_not_found=true` | Heartbeat not found | tooltip 保留 busy、連線及未連線的失敗／重試 |
| `is_connected=true` | `is_busy=true` 為執行中；否則 IDLE 為已連線、DISCONNECTED 正規化為 IDLE，其餘未對應值為未連線 | 忽略殘留 `connect_failed`、失敗次數、重試欄位，不當成目前異常；tooltip 以 `is_connected` 為準 |
| `is_connected=false` 且 `connect_failed=true` | Connection failure | 優先於 retrying／倒數；tooltip 保留重試進度、失敗次數與最後錯誤 |
| `is_connected=false` 且 `connect_fail_count>=1` 且 `retrying=true` | 重試中 `min(n+1, retry_threshold)/retry_threshold` | n 為 Core 失敗次數；tooltip 保留實際 Offline 與失敗欄位 |
| `is_connected=false` 且 `connect_fail_count>=1` 且 `retrying=false` | 重試倒數，次數 `min(n, retry_threshold)/retry_threshold` | 倒數讀 `retry_in_sec`；null 保持未知、不捏造倒數；tooltip 保留失敗欄位 |
| 其餘（無失敗計次） | `is_busy=true` 為執行中；否則依 `status`：IDLE 為已連線、DISCONNECTED 為未連線；其餘未對應值為未連線 | 即使 `status=IDLE/BUSY` 與 `is_connected=false` 不一致，連線燈與 tooltip 仍為 Offline，不自行修正 Core 資料 |

`status=FROZEN` 不能取代 `is_frozen` 判定；Core 有效資料兩者應一致（BUSY 可遮蔽 FROZEN），不一致樣本不得由 status 新增凍結或執行中；這是欄位對應決策表的來源限制，不照抄 Tk 的 FROZEN／BUSY fallback。`IDLE`／已連線不代表 Log READY 或產品 PASS。Core 有效連線時，凍結且 KVM 斷線仍保留 Frame frozen，連線燈獨立 Offline；Core 斷線則依下表全列改為「未連線」。

- **Core 斷線顯示決策表**：採 user 2026-11-27 20:28，取代 #129 決定 B，適用本階段 WPF。Core 斷線時停止舊倒數即時遞減，所有列（包括原紅列）改為灰底「未連線」，不保留 Last status，每列 KVM 連線燈均為 N/A。依據現行 Tk：`host-app/ui_app.py:1611-1615`、`host-app/station_presentation.py:63-68`（`present_station(..., core_connected=False)` 回 `_DISCONNECTED`）、`host-app/ui_app.py:1367-1372`（N/A 虛線圈）；列文字沿用 `station_offline`（`host-app/ui_i18n.py:348`）。

| 情境 | 設備列 | 橫幅 |
| --- | --- | --- |
| 斷線前有紅色異常 | 全列灰底「未連線」、KVM 燈 N/A，無 Last status | 沿用 Tk 服務離線提示「⚠ 未連上服務」 |
| 斷線前沒有紅色異常 | 全列灰底「未連線」、KVM 燈 N/A，無 Last status | 沿用 Tk 服務離線提示「⚠ 未連上服務」 |
| 從未取得成功資料 | 無可用狀態，不捏造設備或正常資訊 | 沿用 Tk 服務離線提示「⚠ 未連上服務」 |
| Core 恢復有效資料 | 依新資料更新；仍異常的列依即時資料顯示紅色，不使用 Last status | 依新資料更新即時異常摘要 |

服務離線提示依 `dash_offline`（`host-app/ui_i18n.py:399`、`host-app/ui_app.py:1438,1455`），不保留舊紅橫幅。

- **Core 中斷／重啟決策表**：沿用 Tk／PROTOCOL 的成功判讀；僅 envelope 匹配且有效的 `status=ok` `core_status` 可更新即時設備狀態。Core 重啟判定為「曾成功連線、中斷後恢復，或 `core_status.uptime_seconds` 比前次有效值變小」。逾時、重連及初次啟動寬限期採文末第 8 項：輪詢 1.5 秒、建連最多 2 秒、讀寫 2 秒、starting 30 秒，其後 waiting，超過 120 秒轉紅。

| 事件 | Core 顯示狀態 | 設備列怎麼處理 |
| --- | --- | --- |
| 首次尚未取得有效 `core_status` | 尚未連上／不可用（starting 30 秒，其後 waiting，超過 120 秒轉紅） | 無可用狀態，不虛構正常設備 |
| 有效匹配的 ok `core_status`，無重啟條件 | 即時 | 依新清單與優先序表更新 |
| 任何非 ok `core_status`，含 `server_shutting_down`、`internal_error` | Core 中斷；不是即時成功；從未成功連上時，仍顯示尚未連上／不可用 | 依 Core 斷線顯示決策表全列灰底「未連線」、KVM 燈 N/A，無 Last status |
| 連線失敗、斷線、逾時、取消、格式錯誤、envelope 不匹配回覆 | Core 中斷（從未成功則仍為尚未連上／不可用） | 同上；清理失效連線、忽略晚到回覆，不更新成功快照 |
| 中斷後恢復有效 `core_status`，或有效回覆的 `uptime_seconds` 變小 | 即時，附 Core 重啟／恢復提示 | 依新資料恢復即時顯示；latch 清除不等於設備修復 |
| 僅測試連線 `ping` 成功 | 僅證明測試連線成功 | 不以 ping 更新設備資料或恢復即時顯示，等待有效 `core_status` |

禁止因 Core 重啟後 latch 清除而顯示或記錄「異常已解除」「已修復」或同等文案；即使新資料不再紅色，亦不得宣稱設備已修復。
- **觀察能力限制**：`core_status` 不讀影格、不拿設備鎖；本階段沒有 inspect，因此輪詢不觀察 PTS、不能解除凍結，也不會自行刷新畫面心跳證據。相關讀取統計只是既有讀取留下的資料，不是 WPF 新做的檢測。不提供解除凍結或故障復位操作。
- **工具鏈與交付**：依 ADR-0009 使用 `dotnet` CLI、VS Code 開源 C# 擴充套件 `ms-dotnettools.csharp`（不裝 C# Dev Kit）與 GitHub Actions，不使用 Visual Studio Community；採用相依版本前核對授權。Windows CI 必須執行 restore／build／test／publish，產物可供現場測試。採 .NET 10、Windows 11，交付含 .NET 的 self-contained ZIP、不簽章，不含安裝精靈；版本與打包已批准（user 2026-11-27）。

## Testing Decisions

- **假 Core 協定行為測試**：以可控制回覆與斷線的假 Core，透過真實 TCP 驗證連線、`ping`／`core_status`、LF 分包、拆包／合包、共同欄位與 envelope 核對、錯誤回覆、逾時、取消、晚到／不匹配回覆，以及斷線重連與 Core 重啟。假 Core 樣本對照 `tests/test_protocol_envelope.py` 與 `tests/test_tcp_server.py` 的 envelope、錯誤 data、拆包／合包案例。不以實作私有欄位或呼叫次數代替行為驗證。
- **現行 Core 相容性**：取得現行 Python Core 的成功與錯誤回覆樣本，驗證 §5.9 nullable 例外、空設備清單、未讀取統計與實機 8 台清單資料；請求僅含現行允許欄位，不送 LabVIEW `status` 或其他設備指令。不為通過測試改動協定；`python -m pytest tests/test_core_protocol.py tests/test_protocol_envelope.py tests/test_tcp_server.py` 必須在不改任何測試的前提下全數通過。
- **畫面行為**：涵蓋 8 台及非 8 台（含捲動）、空清單、上述決策表各列、`is_busy=true` 且 `is_frozen=true` 必須顯示 Frame frozen、同時心跳停止與找不到心跳框、凍結且斷線、連線失敗與重試倒數、執行指令中、Core 首次不可用、Core 斷線後全列灰底「未連線」、無 Last status、KVM 燈 N/A、恢復即時更新及 Core 重啟。確認警示不被低優先狀態覆蓋、不把未知資料顯示正常；語言沿用 Tk 雙語與切換、畫面／tooltip 顯示讀取統計；未批准的視覺案例待第 7 項決定。
- **安全與隔離**：驗證 WPF 啟閉及重連只產生 `ping`／`core_status` 流量，不送 `snapshot`、`inspect`、`hid`、`shutdown` 或設備操作；輪詢不讀影格、不清凍結、不改 device lock 或 HID handoff。與 LabVIEW 並用時，WPF 退出不得中止 Core 或測試流程。
- **Windows CI**：在 Windows runner 執行選定 SDK 的 restore／build／test／publish，檢查套件可產出；在選定部署模式驗證必要資源與執行條件，不能只以編譯成功代表可部署。
- **實機測試不可由 CI 代替**：在 Windows 11 上照 Core 清單（實機 8 台：DFU-01～06、FCT-01、BT-01）測試 ZIP 啟動、設備狀態、Core 不可用／重啟、Monitor↔Core TCP 與 KVM 斷線恢復、與 LabVIEW 並用，以及 100%／125%／150% DPI 可讀性與 tooltip／警示不截字；不測 200% DPI。採案 B：長跑 2 小時；Core 程序仍在的 TCP 斷線須於 10 秒內恢復，KVM 斷線須於 30 秒內恢復，Core／KVM 各須連續 3 次通過。時間依據見第 9 項；Core 程序被殺的 watchdog 恢復另記、不計入 10 秒上限。記錄套件版本、Core 版本、Windows／DPI、設備配置與結果；部分實機搭配模擬須明示，不得宣稱已驗證 8 台實機。並存已定，啟動／切換／回退方式待第 4 項決定後演練，對應 #227／#228。實機測試站名：待使用者補（user 2026-11-27）。

## Out of Scope

- stream／`snapshot`、inspect、HID control、pop-out 手動操作，以及 Pattern 維護或其他完整 Monitor UI 功能移植。
- Core 改寫、搬到 .NET、WebRTC／影像辨識重寫、LabVIEW 流程變更及任何協定變更。
- Log Solution 顯示契約、標記、定位點、結果色帶、判讀幾何與安全規則變更。
- Core 自動啟動／重啟管理及完整本機故障診斷（含區分設定檔、port、程序管理等完整燈號原因）；僅憑 status socket 失敗不得宣稱已知本機根因。
- 不含安裝精靈；已定交付 ZIP、不簽章、含 .NET（self-contained）（user 2026-11-27）。
- 本階段不實作 Tk 全面改版或默認汰換 Tk；並存已定，切換／回退／啟動責任與汰換時機仍待決（user 2026-11-27）。

## 使用者決定與待補事項

以下第 1–10 項及補充來源為 user 2026-11-27；第 11 項另註覆寫時間。

1. **.NET 版本**：第一階段採 .NET 10。
2. **目標 Windows 版本**：目標 Windows 11。
3. **打包／安裝**：交付 ZIP、不簽章、含 .NET（self-contained），已完全定案；不含安裝精靈。
4. **Tk 與 WPF 並存**：第一階段並存已定。如何啟動、切換、回退、何時汰換 Tk，以及只開 WPF 時由誰啟動 Core，仍待決。WPF 無 stream／inspect／HID，不能默認取代完整 Tk 工作流程；現行 Tk 會自動啟動 Core.exe，本階段不實作自動啟動管理。
5. **UI 語言**：沿用現行 Tk 雙語與語言切換，不改協定 English ASCII 限制。
6. **實機測試硬體**：照 Core 清單（實機 8 台：DFU-01～06、FCT-01、BT-01）。依 Core 清單筆數呈現，不寫死 8 列；非 8 台（含捲動）亦須可用，空清單不補造設備。實機測試站名：待使用者補。
7. **讀取統計與視覺規格**：第一階段畫面／tooltip 顯示讀取統計已定。Cruella machine-status 設計稿、版面、配色、字型、警示、倒數、閃爍，以及如何去除預覽／操作區仍待決；外部設計稿不自動轉成已批准決策，Core 斷線顯示依第 11 項。
8. **輪詢與重連參數**：沿用現行 Tk，不套用 LabVIEW 的 5 秒／90 秒：
   - status 輪詢間隔 1.5 秒：`host-app/ui_app.py:1515`（`self.after(1500, self._poll_status)`）。
   - Core TCP 建連逾時最多 2 秒：`host-app/core_channel.py:167`（`min(timeout, 2.0)`）；手動連線預設 2 秒：`host-app/ui_app.py:320`。
   - status 讀寫逾時 2 秒：`host-app/ui_app.py:343`、`host-app/core_channel.py:181-188`。
   - 重連沿用 1.5 秒輪詢排程，socket 不存在時由下次請求重建：`host-app/core_channel.py:165-167`、`host-app/ui_app.py:1515`。
   - 手動重試入口沿用 Tk 的 🔄／重連按鈕：`host-app/ui_app.py:677`（`btn_connect`）、`:1743`。
   - 初次啟動寬限期 starting 30 秒：`host-app/core_indicator.py:27,132-133`（`START_GRACE_S = 30.0`）；其後 waiting，超過 120 秒轉紅：`windows/core-scheduler.json`（`red_threshold_seconds: 120`、`interval_minutes: 1`）、`host-app/core_indicator.py:135-140`。
9. **實機通過門檻**：採案 B，長跑 2 小時；Monitor↔Core TCP 斷線（Core 程序仍在）10 秒內恢復，KVM 斷線 30 秒內恢復，Core／KVM 各連續 3 次通過。Core 10 秒來自 1.5s 輪詢 + 2s 建連 + 2s 讀取再留約一輪；KVM 30 秒來自一輪最壞約 5+10+1+10+1.5=27.5 秒（`host-app/kvm_pool.py` 的 heartbeat 5s、`connect_retry_interval_sec` 10s、`retry_tick_sec` 1s、`connect_timeout_sec` 10s）。Core 程序被殺時，watchdog 退避 5／10／20 秒（`host-app/core_daemon.py:762-785`）不計入 10 秒上限，實機紀錄須分開註明。測 100%／125%／150% DPI，不測 200% DPI；不以 CI 取代現場證據。
10. **PROTOCOL 文件補欄位**：同意另開純文件票 [#229](https://github.com/Bowei1121/B518_KVM_Log/issues/229)，補 §5.9 的 `is_busy`／`is_frozen`／`is_connected` 說明；僅補文件、不改協定，本階段規格 PR 不修改 PROTOCOL.md。
11. **Core 斷線顯示**：user 2026-11-27 20:28，取代 #129 決定 B；本階段 WPF 不再採用舊決定。Core 斷線時所有列（含原紅列）皆灰底「未連線」、不保留 Last status，每列 KVM 連線燈 N/A，與現行 Tk 相同；以後若要再改，須使用者重新決定。

## Further Notes

- B 案估算為 7～12 人日，包含驗證與修正、不含等待 Windows 環境的日曆時間；它是規劃背景，不是交期承諾，未決選項改變時須重新評估。
- 來源為交辦單所附 B 案估算與 #221 追蹤票內容；不將暫存來源拷入 docs，也不把追蹤票歷史核取方塊當成本 spec 的驗收結果。
- 狀態決策追溯至 [#129](https://github.com/Bowei1121/B518_KVM_Log/issues/129)（歷史決定 B，已由 user 2026-11-27 20:28 取代）、[#130](https://github.com/Bowei1121/B518_KVM_Log/issues/130)（找不到心跳框）、[#134](https://github.com/Bowei1121/B518_KVM_Log/issues/134)（連線失敗異常）已決定條目。Cruella 外部設計稿不在本 repo，其未批准視覺項仍待第 7 項決定；完整本機故障診斷不在本階段範圍。
- ADR-0009 保留的歷史段落與估算含有先前順序、版本及完整四 channel 的方向；本階段依 Accepted 狀態、使用者選 B 與本 spec 硬限制只實作 status channel，其餘 channel 留待後續另行定義。
