# Spec: Monitor UI 改用 C# WPF 第一階段（status 畫面）

GitHub issue：https://github.com/Bowei1121/B518_KVM_Log/issues/221（追蹤票）。

子票待開，開出後補上。

相關文件：`CONTEXT.md`、`docs/PROTOCOL.md`（§1～3、§5.1、§5.9、§6～7）、`docs/LOG_SOLUTION_CONTRACT_1.2_HANDOFF.md`（含後續顯示契約修訂）、ADR-0002（device lock／fail-fast）、ADR-0004（Core 與 UI 分離）、ADR-0005（Frame Freeze Guard）、ADR-0008（手動 HID handoff）、ADR-0009（C# WPF 與 Python Core 邊界）、`docs/specs/0004-kvm-connection-command-timing.md`（格式參考）。

決策依據：ADR-0009 已於 2026-11-23 Accepted，使用者決定現在開始、先看對照圖再選方案；2026-11-25 11:00 Asia/Taipei，經國務卿 → 專案夥伴轉述原話：「選 B：WPF 第一階段」。本 spec 定義 B 案範圍；歷史追蹤票的 Proposed 與啟動前提描述不代表目前仍未選案。版本、部署及視覺選項仍依文末問題待決，不因估算舉例而視為批准。

## Problem Statement

現有 Monitor UI 為 Tkinter TCP client。現場需要開始建立 Windows 原生介面的替換路徑，同時保留已驗證的 Python Core、LabVIEW 通訊及設備安全規則。若直接移植全部 Monitor UI，會把 stream、inspect、HID 操控與部署決策一起帶入，無法清楚驗收第一階段。

本階段必須讓操作員與現場工程師看九台設備的狀態、辨別 Core 不可用與 KVM 異常，且不把上次資料誤當即時狀態。WPF 的輪詢本身不觀察影格，不能宣稱已檢查畫面活性或修復設備。

## Solution

建立獨立的 C# WPF status 用戶端，僅透過 status channel 的 `ping`／`core_status` 連線既有 Python Core。工作包為 .NET 專案（含連線設定與用戶端記錄）、TCP 連線與重連、九台設備 status 畫面、Windows CI 與打包、實機測試。

一次 `core_status` 取得完整設備清單與狀態，不為九台設備各開一條 status socket。畫面呈現 Core 連線狀態、設備狀態、異常警示、背景重連進度與 Last status；不提供畫面預覽或設備操作。後續開發票以本 spec 為範圍與驗收依據，未決選項須先取得使用者決定，不能由實作者默認填入。

## User Stories

1. 身為操作員，我希望在同一個 status 畫面看九台設備，以統一設備 ID 與站別辨識各列，不必用 IP 猜測設備。
2. 身為現場工程師，我希望連上 Core 後取得設備清單與目前狀態，以便與既有 Python Core 的配置一致，不另維護一份設備來源。
3. 身為操作員，我希望辨別 WPF 到 Core 的連線、KVM 的連線及設備異常，以便知道是哪一層不可用。
4. 身為操作員，我希望 Core 斷線後用戶端能重連，且不凍結畫面；Core 恢復後以新回覆更新狀態。
5. 身為操作員，我希望 Core 從未連上時明確顯示不可用，不把空白資料當作九台設備正常，並提供連線位址及可理解的錯誤提示。
6. 身為操作員，我希望異常依既有優先序呈現，警示摘要、設備列與 tooltip 一致，不因設備正在執行指令就蓋掉畫面凍結或心跳異常。
7. 身為操作員，我希望看到 Core 回報的 KVM 重試倒數、重試中與失敗次數，能區別恢復中與連線失敗異常。
8. 身為操作員，我希望 Core 不可用時保留最後一次成功取得的設備狀態並標為 Last status，清楚知道它不是目前狀態；未取得過的資料不得捏造。
9. 身為 LabVIEW 測試流程控制者，我希望 WPF 開啟、關閉、崩潰或重連都不影響 Core 與測試流程，也不送出設備操作或重啟 Core。
10. 身為現場工程師，我希望 status 用戶端保留顯示契約、Frame Freeze Guard、device lock 與 HID handoff 的邊界，不因 UI 改寫而繞過安全防護。
11. 身為維護者，我希望 Windows CI 可重現建置、測試及產出套件，以便在使用者選定的 Windows 與部署方式驗證交付。
12. 身為現場工程師，我希望用真實 Windows 與 KVM 環境確認九台狀態、Core 重啟、斷線恢復及高 DPI 顯示，實機數量與站別先由使用者決定。

## Implementation Decisions

- **Core 邊界（硬限制）**：Core 留在 Python；本階段不改寫 Core、不把 Core 搬到 .NET。WebRTC、KVM Connection Pool、辨識流水線與自動測試流程仍由 Core 負責。依 ADR-0004，WPF 與 Core 的行程、生命週期獨立；關閉 WPF 只清理自身資源，不送 `shutdown`。
- **協定相容（硬限制）**：`docs/PROTOCOL.md` 保持相容，本階段不做協定變更，不新增／改名指令或欄位，不改 envelope、錯誤碼、設備識別、長輪詢、結果與輪次語意。
- **顯示契約（硬限制）**：Log Solution 顯示契約不動；列數標記（排數格）、狀態標記、錨點／定位點、結果色帶與判讀幾何不得更改、不得蓋住、不得要求契約變更。不因交接文件檔名含 1.2 就退回舊幾何；遵守現行文件的後續修訂。
- **安全規則（硬限制）**：Frame Freeze Guard、device lock（ADR-0002 fail-fast）與 HID handoff 必須保留，不得破壞或繞過。status 用戶端只走 `ping`／`core_status`，不得送會影響凍結、鎖或 HID 工作階段的指令，亦不得直接連 KVM。既有手動操作用戶端的交接規則仍由 Core 維持。
- **協定語言（硬限制）**：Core／TCP 協定字串僅限 English ASCII，包含 `message` 與錯誤文字；UI 翻譯與本機記錄屬不同層，不把中文送入協定。
- **status channel**：此處 status 是 client 的連線分工，使用 `ping`／`core_status`，不是 LabVIEW 的 `status` 指令。兩個請求的 `device` 均為 `""`，帶現行共同欄位；同一 socket 一次只送一筆，收到完整回覆後才送下一筆。遵守 UTF-8 無 BOM、LF 分包與 `protocol_version="1.0"`；TCP 分段或一次收到多行時須正確組包、分行，不能把一次讀取等同一筆 JSON。
- **回覆與錯誤**：核對 envelope 的 `req_id`、`device`、`cmd` 與版本，再依 `status`／`error` 判斷；不能解析 `message` 決定狀態。逾時、取消、錯誤回覆、格式錯誤或不匹配回覆不能覆蓋為成功狀態；失效連線須清理，避免晚到回覆套用至新請求。背景輪詢與重連不得阻塞 WPF UI 或造成重疊請求。
- **nullable 相容**：依 §5.9 為 `retry_in_sec`、`first_fail_at`、`last_connect_error` 及讀取統計等欄位保留 nullable 語意。這是相對 §1「不使用 null」通則的既有例外，須以現行 Core 回覆驗證；不得為了 DTO 方便而改 Core、改協定或把未知值當成正常值。
- **設備與重連責任**：以 Core 設備清單及統一設備 ID 為準，呈現九台設備。WPF 只重連到 Core；KVM 連線重連、失敗計次與狀態解除由 Core 負責。重試次數門檻與倒數使用 §5.9 現有欄位，不由 WPF 另算失敗門檻或發動 KVM 重連。
- **狀態與警示語意**：沿用 `CONTEXT.md` 的畫面凍結 > 心跳停止 > 找不到心跳框 > 執行指令中／已連線優先序；連線失敗異常亦須可見，不掩蓋更高優先異常。KVM 連線狀態獨立呈現，凍結且斷線仍保留 Frame frozen 警示。警示摘要與 tooltip 須能指出設備 ID、異常類別與實際連線狀態。不得把 Core `BUSY` 當成 Log BUSY 或監控就緒；`IDLE`／已連線也不代表 Log READY 或產品 PASS。色彩、排版、圖示、閃爍與各異常間的視覺呈現細節依未釐清問題確認，不默認採用外部設計稿。
- **Last status**：Core 中斷後保留最後成功快照，所有保留資訊與警示明示 Last status，停止把舊倒數當成即時倒數；重新取得有效 `core_status` 後才恢復即時顯示。Core 從未連上時顯示無可用狀態。不得把 Core 重啟造成的 latch 清除當成設備已修復。
- **觀察能力限制**：`core_status` 不讀影格、不拿設備鎖；本階段沒有 inspect，因此輪詢不觀察 PTS、不能解除凍結，也不會自行刷新畫面心跳證據。相關讀取統計只是既有讀取留下的資料，不是 WPF 新做的檢測。不提供解除凍結或故障復位操作。
- **工具鏈與交付**：依 ADR-0009 使用 `dotnet` CLI、VS Code 開源 C# 擴充套件 `ms-dotnettools.csharp`（不裝 C# Dev Kit）與 GitHub Actions，不使用 Visual Studio Community；採用相依版本前核對授權。Windows CI 必須執行 restore／build／test／publish，產物可供現場測試。具體 .NET 版本、Windows 版本與打包／安裝方式保持待決，不能把估算中的 .NET 10 LTS、self-contained win-x64 ZIP 當成已批准規格。

## Testing Decisions

- **假 Core 協定行為測試**：以可控制回覆與斷線的假 Core，透過真實 TCP 驗證連線、`ping`／`core_status`、LF 分包、拆包／合包、共同欄位與 envelope 核對、錯誤回覆、逾時、取消、晚到／不匹配回覆，以及斷線重連與 Core 重啟。不以實作私有欄位或呼叫次數代替行為驗證。
- **現行 Core 相容性**：取得現行 Python Core 的成功與錯誤回覆樣本，驗證 §5.9 nullable 例外、空設備清單、未讀取統計與九台設備資料；請求僅含現行允許欄位，不送 LabVIEW `status` 或其他設備指令。不為通過測試改動協定。
- **畫面行為**：涵蓋九台混合狀態、優先序、同時心跳停止與找不到心跳框、凍結且斷線、連線失敗與重試倒數、執行指令中、Core 首次不可用、斷線後 Last status、恢復即時更新及 Core 重啟。確認警示不被低優先狀態覆蓋、不把未知資料顯示正常；最終語言與視覺案例依使用者決定補齊。
- **安全與隔離**：驗證 WPF 啟閉及重連只產生 `ping`／`core_status` 流量，不送 `snapshot`、`inspect`、`hid`、`shutdown` 或設備操作；輪詢不讀影格、不清凍結、不改 device lock 或 HID handoff。與 LabVIEW 並用時，WPF 退出不得中止 Core 或測試流程。
- **Windows CI**：在 Windows runner 執行選定 SDK 的 restore／build／test／publish，檢查套件可產出；在選定部署模式驗證必要資源與執行條件，不能只以編譯成功代表可部署。
- **實機測試不可由 CI 代替**：在使用者選定的 Windows、站別與硬體上測試套件啟動、九台狀態呈現、Core 不可用／重啟、WPF 與 Core 斷線恢復、KVM 斷線時顯示、與 LabVIEW 並用，以及 100%／125%／150% 高 DPI 的可讀性與 tooltip／警示不截字。記錄套件版本、Core 版本、Windows／DPI、設備配置及結果；若只接部分實機，其餘模擬須明示，不能宣稱已驗證九台實機。測試硬體、長跑時數與通過門檻待使用者決定後補入子票。

## Out of Scope

- stream／`snapshot`、inspect、HID control、pop-out 手動操作，以及 Pattern 維護或其他完整 Monitor UI 功能移植。
- Core 改寫、搬到 .NET、WebRTC／影像辨識重寫、LabVIEW 流程變更及任何協定變更。
- Log Solution 顯示契約、標記、定位點、結果色帶、判讀幾何與安全規則變更。
- Core 自動啟動／重啟管理及完整本機故障診斷（含區分設定檔、port、程序管理等完整燈號原因）；僅憑 status socket 失敗不得宣稱已知本機根因。
- 安裝精靈與程式碼簽章不含於 B 案估算；是否納入須使用者另決定並調整範圍。ZIP、self-contained 等打包選項尚未定案。
- 本階段不實作 Tk 全面改版或默認汰換 Tk；兩者並存與切換策略仍待決。

## 未釐清問題

1. **.NET 版本（待使用者決定）**：第一階段採哪個 .NET SDK／Runtime 版本？ADR-0009 與估算提及 .NET 10 LTS，仍須明確確認本階段版本後才能固定建置與部署需求。
2. **目標 Windows 版本（待使用者決定）**：支援哪些 Windows 版本、版本組建與處理器架構？既有現場 Windows 11 背景不等於已批准本階段支援矩陣。
3. **打包／安裝（待使用者決定）**：採 ZIP、安裝精靈或其他交付方式？採 self-contained 還是依賴已安裝 Runtime？是否要求程式碼簽章？估算未含安裝精靈與簽章，若納入須另估。
4. **Tk 與 WPF 並存（待使用者決定）**：第一階段是否並存，如何啟動、切換與回退，何時汰換 Tk？WPF 無 stream／inspect／HID，不能默認已能取代完整 Tk 工作流程。
5. **UI 語言（待使用者決定）**：第一階段採哪種 UI 語言，是否跟隨既有 Tk 的雙語／語言切換？此決定不改 English ASCII 協定限制。
6. **實機測試硬體（待使用者決定）**：使用哪一站、幾台 KVM 與上位機，是否必須九台實機同時測試？若採部分實機搭配模擬，哪些項目仍需九台現場補驗？
7. **視覺規格（待使用者決定）**：是否採用 Cruella machine-status 設計稿；採用哪些九台版面、配色、字型、警示橫幅、tooltip、重試倒數與閃爍規則？status 第一階段如何去除預覽／操作區？外部設計稿僅為參考，不自動轉成已批准決策。
8. **輪詢與重連參數（待使用者決定）**：status 輪詢間隔、請求逾時、重連間隔／停止條件與手動重試入口採哪些值與操作方式？不可直接套用 PROTOCOL 內供 LabVIEW 使用的 5 秒／90 秒規則作為 WPF 決策。
9. **實機通過門檻（待使用者決定）**：長跑多久、斷線恢復可接受多久，是否增加 200% DPI 與其他部署環境？確認後補入開發票的驗收，不以 CI 通過取代現場證據。

## Further Notes

- B 案估算為 7～12 人日，包含驗證與修正、不含等待 Windows 環境的日曆時間；它是規劃背景，不是交期承諾，未決選項改變時須重新評估。
- 來源為交辦單所附 B 案估算與 #221 追蹤票內容；不將暫存來源拷入 docs，也不把追蹤票歷史核取方塊當成本 spec 的驗收結果。
- `/workspace/b518-hmi/machine-status/spec.md`（讀得 v5.6）是 Cruella 的外部 status 畫面設計參考，不在本 repo main；其視覺規格與完整本機故障診斷未因被引用而批准，採用與否見未釐清問題。
- ADR-0009 保留的歷史段落與估算含有先前順序、版本及完整四 channel 的方向；本階段依 Accepted 狀態、使用者選 B 與本 spec 硬限制只實作 status channel，其餘 channel 留待後續另行定義。
