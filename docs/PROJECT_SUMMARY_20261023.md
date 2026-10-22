# 專案摘要 2026-10-23：HMI 實機回饋修正（#115、#117）

接續 `PROJECT_SUMMARY_20261022.md`「HMI 改善系列」。結束時 GitHub main 在 `31ebe19`，版本 **v0.1.01**；Gitea `origin` 同步因連不上 `10.64.76.34:3000` 尚未完成，需在公司網路內補推（`git fetch github main && git push origin github/main:refs/heads/main`）。

## 1. Windows 11 實機（FIELD_VERIFICATION H1）回饋

- 第一次（v0.0 系列，#107 後）：DPI、emoji、強制淺色、中英切換通過；版面問題（Log 被擠掉、燈號被切、按鈕截字、設備設定欄位與勾選框、空狀態無提示）→ #115。
- 第二次（v0.1.00）：logo、標題、拱橋圖示、截字、勾選框通過；視窗底部被工作列蓋住、開啟位置未貼齊、設備設定換行怪 → #117。注意：改解析度／縮放後必須**重開程式**（Log 第一行啟動時間可用來判斷），否則結果無效。

## 2. 完成的票

| 票 | 內容 | PR | 審查 | 版本 |
| --- | --- | --- | --- | --- |
| #115 | 開啟尺寸、頂列（FOXLINK logo 左／🌐⚙ 右）、按鈕不截字、設備設定 `ttk.Checkbutton`＋水平捲動、空狀態提示、Log 自動換行、藍底白拱橋點陣圖示（`tools/` 產生 PNG／.ico）、`host-app/VERSION`、標題 `ATE - LabVIEW Bridge Monitor v<ver>`、CI 寫入 build hash | #116 | 4 輪 | 0.1.00 |
| #117 | 視窗貼齊工作區上緣與右邊（`SPI_GETWORKAREA`＋外框）、不超出工作區、空間不足時預覽先縮（下限 DPI 換算 × 0.7）、切換語系時視窗大小與元件位置不變（寬度取兩語系較長者）、診斷結果固定 3 行／快照資訊 1 行＋`…`＋tooltip、全域滑鼠滾輪（Shift＝水平） | #118 | 4 輪 | 0.1.01 |

## 3. 使用者決定（重點）

- 支援矩陣：現場 26 吋 **1920×1080 @100%**；測試筆電 2880×1800 @100～200%。不支援更小螢幕或 1920×1080 高倍率。
- 版本 `0.x.xx`（測試版），每合併一張功能票 patch +1，由主 session 合併時負責。exe／spec／artifact 名稱暫維持 `AtlasKVM`，另開票再改。
- 預覽：開啟寬度 ≥ 螢幕寬 50%；高倍率空間不足時最小縮到 70%。
- 動態長文字以固定行數＋`…`＋tooltip 呈現，全文在 Log（依「HMI 簡短、詳細進 Log」與「元件位置不隨語系變」原則）。
- 整體版面重排之後另開 grilling 討論再開票。

## 4. 流程教訓

- **CI runner 是 Windows、實體螢幕 1024×768、Segoe UI**：版面測試的螢幕模擬必須同時替換 screen 尺寸、工作區、外框，以及**每個 toplevel 的 `wm_maxsize`**；Windows 上 `geometry()` 後要 `update()` 才生效。macOS 綠燈不代表 Windows 綠燈。
- 測試只比較 `winfo_width` 與 `reqwidth` 抓不到固定寬度造成的裁切，需用 `font measure`。
- **換模型規則**（使用者 2026-10-22 定）：Haiku 實作同一 PR 約 3 輪審查不過，就停掉並改 Sonnet 接手同一 PR（自己 branch reset 到 PR head，`git push github HEAD:<PR branch>`）。#118 Sonnet 接手後一輪即 CI 全綠、審查通過。
- 背景 pytest 一次只跑一組、加 `faulthandler_timeout`，殘留行程會佔 port 讓 `test_monitor_start.py` 卡死。

## 5. 待辦

- 使用者在 Windows 11 重跑 H1（v0.1.01）：工作區定位、Log 6 行、語系切換不跳動、滾輪。
- Gitea `origin` main 補同步。
- 整體版面優化 grilling → 新票。
- 仍開著：#111（Windows 既有測試失敗）、#69、#13 等，見前日摘要。
