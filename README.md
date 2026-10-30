# B518 JetKVM + Log

此 repo 是 B518 的 JetKVM 畫面控制 prototype：上位機透過 JetKVM 取得測試機串流畫面、判讀畫面結果，並以 USB HID 操作測試機鍵盤與滑鼠。

Repo 名稱保留 JetKVM_Log 以維持既有 Git 與部署識別；測試機本地 Log 的讀取與解析由另一個獨立專案負責，本 repo 不讀取 Log、不定義 CSV 格式，也不與該專案建立程式依賴。

## 目錄

- `host-app/`：上位機、自動化與打包原始碼。
- `config/`：設備清單範例（`devices.example.json`）與設定說明。
- `docs/`：研究、架構與驗證紀錄。
- `tests/`：pytest 測試，執行 `python -m pytest -q`（需要 bind localhost，受限沙箱內會失敗）。
- `tools/`：管理與驗證工具，包含 Core 管理（`manage_core.py`）與錄影重播壓測（`replay_load_test.py`）。
- `third_party/jetkvm/`：固定版本的 JetKVM private mirror submodule。

## 設備清單與通訊協定

設備清單是上位機唯一的設備設定來源，檔案位於 `~/Documents/template/devices.json`，不隨程式打包、不進 Git。格式與範例見 `config/devices.example.json`：每台設備以設備 ID（`<站別>-<兩位數>`，例如 `DFU-01`）為 key，記錄站別、KVM IP 與測試通道數。Core Service 缺檔或內容有誤時拒絕啟動並列出錯誤；修改後需重啟 Core。Monitor UI 的「⚙ 設備設定」頁可編輯此檔並在儲存前驗證，儲存時會備份舊檔。

LabVIEW 與 Core 之間的協定為 TCP JSON 1.0：每行一筆 JSON、以 `req_id` 配對回覆、只帶設備 ID。例如：

```json
{"protocol_version":"1.0","req_id":"6f1d0c2e-0007","device":"DFU-01","cmd":"check_finish","timeout_sec":600}
```

完整欄位、錯誤碼與範例見 [TCP JSON 介面規格 1.0](docs/PROTOCOL.md)。

## 視覺模板

正式模板是上位機使用者可編輯的外部資料，固定存放於 `~/Documents/template`，不隨程式打包：

- `DFU/DFU_<template_key>.png`
- `FCT/FCT_<template_key>.png`
- `BT/BT_<template_key>.png`
- `COMMON/COMMON_<template_key>.png`：三站共用，目前只有 `log_solution_dock_icon`。
- `_captures/jetkvm_frame.png`：原始擷取畫面，不能作為正式模板。

請在「創建Pattern」視窗選擇設備與模板種類；程式會自動決定唯讀檔名及輸出位置。`host-app/FCT` 中的圖片僅保留為歷史參考，執行程式不會讀取它。

各站使用的模板：

- DFU：`window`、`input`、`button`（OK）、`checkbox_checked`、`checkbox_unchecked`、`dock_icon`。checkbox 只需兩種狀態模板，通道數由設備清單決定，不再需要每通道的錨點模板。
- BT：`window`、`start_all`、`dock_icon`。
- FCT：`window`、`dock_icon`。目前 TCP 流程不使用，保留於模板目錄。
- COMMON：`log_solution_dock_icon`。Log Solution 的 Dock 圖示，`monitor_start` 與 `check_start`／`check_finish` 的可見性恢復共用。

`testing`、`pass`、`fail` 結果模板已淘汰，結果判讀全部來自 Log Solution 的顯示契約。

各站支援的 TCP 指令：

- DFU：`input` → `monitor_start` → `start_test`（點 OK）→ `check_start`（可省略）→ `check_finish`。`input` 的 `items` 為 `[{slot, sn}]`，通道 1..N。
- BT：`monitor_start` → `start_test`（點 Start All）→ `check_start`（可省略）→ `check_finish`。
- FCT：`monitor_start` → `check_start`（可省略）→ `check_finish`。`input`、`start_test` 不支援，回 `unsupported_command`。

三站的 `check_start`／`check_finish` 都讀取 Log Solution 顯示契約的畫面，不再依賴模板。`check` 與 `button` 已移除，送出回 `unknown_command`。`monitor_start` 必填 `timeout_sec`（1–300）。交接見 [LabVIEW 設計者交接](docs/LABVIEW_HANDOFF.md)。

## 匹配診斷

主畫面的「匹配結果」會開啟最近一次流程的模板分數、命中狀態與逐步疊圖。每個設備的最近一次結果保存在 `~/Documents/template/_captures/match_diagnostics/<DEVICE>/latest/`；這些資料僅供問題追查，不影響 TCP 回覆或正式 PASS／FAIL 判定。

## 接手與設備流程文件

- [專案開發與接手摘要（2026-09-01）](docs/PROJECT_SUMMARY_20260901.md)
- [TCP JSON 介面規格 1.0](docs/PROTOCOL.md)
- [部署說明（B518 九台）](docs/DEPLOYMENT.md)
- [錄影重播壓測](docs/LOAD_TEST.md)
- [DFU 設備工作流程](docs/workflow_DFU.md)
- [FCT 設備工作流程](docs/workflow_FCT.md)
- [BT 設備工作流程](docs/workflow_BT.md)

## 取得原始碼

```sh
git clone --recurse-submodules <repo-url>
```

既有 clone 請執行 `git submodule update --init --recursive`。

`third_party/jetkvm` 的 `origin` 為公司 private mirror，`upstream` 為 JetKVM 官方 repo。修改第三方程式碼前請先確認 GPL-2.0 的散布與授權義務。

## Ticket 16 維護與配對部署

程式維護與提供更新由使用者負責，當地 TE 工程師協助部署到設備。App 與上位機須按顯示契約 1.3 配對更新；隔離候選、停止現場流程後同步更換、查核與保留既有資料的步驟見 [Ticket 16 配對部署說明](docs/TICKET16_DEPLOYMENT.md)。這些步驟尚未在現場執行，實際 JetKVM／設備／發布 App 與外部 TCP state-only 相容性仍待驗。

ATE 設備只有 SFC 網路，Log App 更新由 TE 人工搬入；上位機工廠內網可用性未定，目前同樣按人工配對更新準備。自動更新列為後續可能性。

顯示契約 1.3 另以 header 的兩格排數標記明確區分一排／兩排。`check_finish` 拒絕缺少排數標記的 1.0 畫面，避免把不可讀的第二排當成單排完成。新本機 Tk 容量回放可用 `tools/verify_ticket16_layout_frames.py --app-evidence <App-layout-evidence> --output <report.json>` 重現，輸入為未分類的 BGR 像素。

Windows 打包產物 `AtlasKVM` 資料夾同時包含 `AtlasKVM.exe` 與無主控台的 `Core.exe`。Core 只監聽 `127.0.0.1:5000`；打包版的 log、PID 與狀態分別位於 `%USERPROFILE%\Documents\logs\core_service.log`、`%USERPROFILE%\Documents\logs\core_service.pid`、`%USERPROFILE%\Documents\logs\core_status.json`（繁體中文 Windows 顯示為 `文件\logs`）。
