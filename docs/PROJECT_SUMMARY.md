# B518 JetKVM + Log — 專案摘要

更新日期：2026-08-28（Asia/Taipei）

## 討論結論

- 本 repo 獨立承載 JetKVM + Log 的上位機程式、設定、測試與文件。
- Arduino + Log 保留在既有 `B518_205_207_ATE` repo；兩者不共用工作樹或 Git 歷史。
- JetKVM 維持為 `third_party/jetkvm` submodule，來源為公司 private mirror；官方來源保留為 submodule 的 `upstream` remote。
- 共用 Log 規格僅在已穩定且確定共用時才另建專用 repo，prototype 階段不建立跨 repo 依賴。

## 目前 Git 基準

- 方案分支：`main`
- JetKVM submodule：private mirror 的 `dev` 分支研究版本
  `b3c29a44d9e2862b8ff7530830781803ce27b060`

## Repo 拆分

- 上位機原始碼移至 `host-app`，保留既有執行時相對路徑。
- JetKVM CDC/TCP gateway 與相容性研究移至 `docs`。
- 可重建的 build、dist、快取與壓縮檔不納入版本控制。
