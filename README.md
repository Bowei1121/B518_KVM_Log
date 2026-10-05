# B518 JetKVM + Log

此 repo 是 B518 的 JetKVM 畫面控制 prototype：上位機透過 JetKVM 取得測試機串流畫面、判讀畫面結果，並以 USB HID 操作測試機鍵盤與滑鼠。

Repo 名稱保留 JetKVM_Log 以維持既有 Git 與部署識別；測試機本地 Log 的讀取與解析由另一個獨立專案負責，本 repo 不讀取 Log、不定義 CSV 格式，也不與該專案建立程式依賴。

## 目錄

- `host-app/`：上位機、自動化與打包原始碼。
- `config/`：站別與 KVM 設定範例。
- `docs/`：研究、架構與驗證紀錄。
- `tests/`：方案測試說明與未來測試。
- `third_party/jetkvm/`：固定版本的 JetKVM private mirror submodule。

## 視覺模板

正式模板是上位機使用者可編輯的外部資料，固定存放於 `~/Documents/template`，不隨程式打包：

- `DFU/DFU_<template_key>.png`
- `FCT/FCT_<template_key>.png`
- `BT/BT_<template_key>.png`
- `_captures/jetkvm_frame.png`：原始擷取畫面，不能作為正式模板。

請在「創建Pattern」視窗選擇設備與模板種類；程式會自動決定唯讀檔名及輸出位置。`host-app/FCT` 中的圖片僅保留為歷史參考，執行程式不會讀取它。

BT 使用六種模板：`BT_window.png`、`BT_testing.png`、`BT_pass.png`、`BT_fail.png`、`BT_start_all.png`、`BT_dock_icon.png`。既有 TCP `button` 指令會操作 Start All；BT 不支援 TCP `input` 指令。FCT 使用五種模板：`FCT_window.png`、`FCT_testing.png`、`FCT_pass.png`、`FCT_fail.png`、`FCT_dock_icon.png`；FCT TCP `input` 與 `button` 均不支援，主畫面 Switch 僅執行 Dock 前景化。

DFU 僅提供 TCP `input` 與 `check`。`input` 可一次帶入多筆 `slot:SN`（例如 `DFU,1,192.168.1.10,input,1:SN123,3:SN789`），依明確 profile 校正外部 DFU 視窗 checkbox、逐筆 Enter、最後只按一次 OK，並送 `Command+Shift+M` 啟動 B518 Log Solution 監控。這個輸入 profile 仍可為 4／7 格；它不控制 Log 結果版型。`check` 直接讀最新 JetKVM BGR frame，依版本化 `KVM_DISPLAY_CONTRACT.md` 辨識 1–20 格與四種狀態，無舊四／七格 Log 結果模板或生產 fallback。它先觀察到監控中，且需 JetKVM 呈現時間戳持續遞增、兩張不同序號的新鮮完整畫面才會一次回覆本輪狀態；缺少時間戳、時間戳倒退或容量在輪次中改變均拒絕取用。待確認回覆暫停。回覆列格式為 `slot::STATUS`，因畫面契約的色帶只提供狀態，不提供 SN；尚未確認外部 TCP 呼叫端是否接受此狀態列格式。設備編號仍須在 `~/Documents/template/device_profiles.json` 對應至 `4slot` 或 `7slot`，但此 profile 僅用於 DFU 輸入。DFU 不支援對外 `button`。

## 匹配診斷

主畫面的「匹配結果」會開啟最近一次流程的模板分數、命中狀態與逐步疊圖。每個設備的最近一次結果保存在 `~/Documents/template/_captures/match_diagnostics/<DEVICE>/latest/`；這些資料僅供問題追查，不影響 TCP 回覆或正式 PASS／FAIL 判定。

## 接手與設備流程文件

- [專案開發與接手摘要（2026-09-01）](docs/PROJECT_SUMMARY_20260901.md)
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

程式維護與提供更新由使用者負責，當地 TE 工程師協助部署到設備。App 與上位機須按顯示契約 1.0 配對更新；隔離候選、停止現場流程後同步更換、查核與保留既有資料的步驟見 [Ticket 16 配對部署說明](docs/TICKET16_DEPLOYMENT.md)。這些步驟尚未在現場執行，實際 JetKVM／設備／發布 App 與外部 TCP state-only 相容性仍待驗。

ATE 設備只有 SFC 網路，Log App 更新由 TE 人工搬入；上位機工廠內網可用性未定，目前同樣按人工配對更新準備。自動更新列為後續可能性。
