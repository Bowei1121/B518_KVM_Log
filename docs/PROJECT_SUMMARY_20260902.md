# JetKVM 專案摘要

更新日期：2026-09-02（Asia/Taipei）

## 目前責任邊界

本 repo 只負責 JetKVM WebRTC 串流畫面、畫面辨識、PASS／FAIL 正式視覺結果與 USB HID 鍵鼠操作。測試機本地 Log 的讀取與解析由另一個獨立專案負責，兩者沒有程式依賴。

## 最近完成事項

- 建立 host-app/template_catalog.py 作為設備、模板種類、命名、路徑與儲存驗證的唯一來源。
- 正式模板固定存於目前使用者的 ~/Documents/template：
  - DFU/DFU_<template_key>.png
  - FCT/FCT_<template_key>.png
  - BT/BT_<template_key>.png
- 原始 JetKVM 擷取圖固定存於 ~/Documents/template/_captures/jetkvm_frame.png，不混入正式模板。
- 模板製作視窗提供唯讀的設備／模板種類選單與完整輸出路徑，禁止人工輸入檔名；同名模板覆寫前需確認。
- GUI、CLI、TCP 指令與辨識流程只讀取新版 Documents 模板路徑；host-app/FCT 圖片保留歷史參考，不再作為 runtime fallback。
- FCT check 的 TCP 回覆格式維持相容：測試中為 action_done,testing，完成結果為 action_done,index:SN:pass|fail,...。

## 驗證狀態

- tests/test_template_catalog.py 已涵蓋三設備命名、非法輸入、首次建立目錄、拒絕覆寫與確認覆寫。
- 已執行 Python 編譯、Markdown 本地連結檢查與 git diff --check。

## Windows 打包

Windows .exe 應在 Windows 實機或 Parallels Windows VM 中打包。於 VM 本機 clone 專案後，在 host-app 執行 build_exe.bat；模板在目標 Windows 使用者的 Documents 目錄內建立，不會包進 exe。
