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
