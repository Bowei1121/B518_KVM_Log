# JetKVM 專案摘要

更新日期：2026-09-03（Asia/Shanghai）

## 專案狀態

本 repo 的責任是透過 JetKVM 取得測試機畫面、判讀畫面狀態與正式 PASS／FAIL 結果，並以 JetKVM USB HID 操作鍵盤與滑鼠。測試機本地 Log 由獨立專案處理，不是本 repo 的功能。

## 最新實作

- 正式模板由 TemplateCatalog 統一管理，根目錄為使用者 Documents 下的 template 資料夾。
- 模板製作 UI 以設備與模板種類的唯讀選單控制命名，支援 DFU、FCT 與 BT。
- 視覺流程只讀取新版模板位置，缺少模板時會回報預期完整路徑並停止。
- BT 模板已精簡為 window、testing、pass、fail、start_all 與 dock_icon；既有 button 指令會操作 Start All，BT input 指令明確不支援。
- FCT 模板已精簡為 window、testing、pass、fail 與 dock_icon；FCT input／button 指令明確不支援，Switch 僅操作 Dock 前景化。
- 每次流程會保存該設備最近一次的模板匹配分數與疊圖；主畫面的「匹配結果」可在重開程式後檢視。
- Windows 打包應在 Windows 實機或 Parallels Windows VM 使用 host-app/build_exe.bat 執行。
- DFU 已改為獨立 profile-aware 視覺狀態機：只提供多 SN `input` 與 Log 視覺判讀 `check`。profile 由使用者 Documents 下的 `device_profiles.json` 映射為 4slot／7slot；input 會 Dock 前景化、校正並複驗各 slot checkbox、按 slot 順序輸入 SN、只按一次 OK，再送出 `Command+Shift+M`。check 會切換到獨立 Log 監控視窗並回傳所有 slot 的 PASS／FAIL／Notest 或 testing；本 repo 不讀取本機 Log。

## Git 狀態

- 公司 Gitea remote：origin。
- GitHub remote：github，已設定但此開發環境尚未完成 GitHub HTTPS 驗證。
- 本次將目前 main 的既有模板功能提交與本摘要推送到公司 Gitea。
