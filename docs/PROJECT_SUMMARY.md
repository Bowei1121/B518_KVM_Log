# B518 JetKVM + Log — 專案摘要

更新日期：2026-09-01（Asia/Taipei）

## 討論結論

- 本 repo 獨立承載 JetKVM 上位機程式、畫面辨識、HID 操作、設定、測試與文件。
- 程式責任為 HDMI 畫面串流、畫面狀態判讀、鍵盤／滑鼠操作與視覺結果回覆。
- 本專案辨識出的 Testing、PASS 與 FAIL 是獨立的正式視覺結果。
- 測試機本地 Log 的讀取與解析由另一個專案負責；本 repo 不讀取 Log、不設計 CSV schema，也不建立跨專案程式依賴。
- Arduino + Log 保留在既有 B518_205_207_ATE repo；兩者不共用工作樹或 Git 歷史。
- JetKVM 維持為 third_party/jetkvm submodule，來源為公司 private mirror；官方來源保留為 submodule 的 upstream remote。
- Repo 名稱雖保留 JetKVM_Log，僅為既有識別，不代表本 repo 承擔本地 Log 功能。

## 目前 Git 基準

- 方案分支：main
- JetKVM submodule：private mirror 的 dev 分支研究版本
  b3c29a44d9e2862b8ff7530830781803ce27b060

## Repo 拆分

- 上位機原始碼位於 host-app，保留既有執行時相對路徑。
- JetKVM CDC／TCP gateway 與 USB HID 相容性研究位於 docs；這些為 JetKVM 能力研究，不是本地 Log 讀取功能。
- 可重建的 build、dist、快取與壓縮檔不納入版本控制。
