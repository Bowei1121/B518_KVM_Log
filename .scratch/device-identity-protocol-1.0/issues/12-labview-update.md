# 12: LabVIEW 端改版至協定 1.0

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/13（Parent #1）

**What to build:** LabVIEW 測試流程控制者依 `docs/PROTOCOL.md` 1.0 改版：只送設備 ID、自產 `req_id`、以固定 typedef 解析回覆、依 `check` 逐格結果做產品處置。VI 為 LabVIEW 二進位檔，需人工實作。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 02（#3）協定 1.0：envelope 與請求驗證、04（#5）`check` 1.0：逐格結果、待確認立即回、格數核對、06（#7）DFU `input` 1.0：items、Log Solution 忙碌拒絕、checkbox 自動排序、07（#8）BT `button` start_all 註冊為正式指令

**Status:** ready-for-human

- [ ] 依 PROTOCOL.md 第 8 節建立 Envelope 與各指令回覆 typedef
- [ ] 每筆請求產生唯一 `req_id`，回覆核對 `req_id`、`device`、`cmd`
- [ ] TCP Read timeout 大於 `check` 的 `timeout_sec`（建議多 30 秒）
- [ ] `kind=paused` 時暫停設備動作；`unknown`、錯誤不判產品 FAIL；PASS 良品，其他（含 STOPPED）不良品
- [ ] 人機畫面以 `status` 顯示連線狀態
- [ ] 更新 repo 內的 TCPIP 測試 VI
