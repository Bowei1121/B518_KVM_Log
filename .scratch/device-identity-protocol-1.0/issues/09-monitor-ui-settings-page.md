# 09: Monitor UI 設定頁

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/10（Parent #1）

**What to build:** 新增設定頁讓現場人員以表格維護設備清單，不需手改 JSON。設定頁直接讀寫本機檔案（Monitor UI 與 Core 同機），Core 未啟動時也能使用。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 01（#2）設備清單：載入、驗證，並以設備 ID 取畫面、02（#3）協定 1.0：envelope 與請求驗證

**Status:** ready-for-agent

- [ ] 表格欄位：設備 ID、站別、KVM IP、測試通道數（數字輸入，非選單）
- [ ] 按鈕採圖示＋簡短文字：新增設備、刪除選取、全部測試、還原、儲存、重啟 Core
- [ ] 儲存前用設備清單模組驗證，錯誤顯示在該列下方，有錯不寫檔
- [ ] 儲存時以時間戳檔名備份舊檔
- [ ] 逐列與全部測試連線，顯示延遲或逾時
- [ ] 儲存後提示需重啟 Core；任一設備忙碌時「重啟 Core」被拒絕並說明哪台在執行
- [ ] 首次無設備清單時預填 B518 九台（通道數 4、規劃 IP）
- [ ] Tk 畫面不做自動化測試；驗證、備份與預填邏輯放在可測試的非 GUI 層並有單元測試
