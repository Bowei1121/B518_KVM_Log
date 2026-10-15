# 06: DFU `input` 1.0：items、Log Solution 忙碌拒絕、checkbox 自動排序

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/7（Parent #1）

**What to build:** 將 DFU 輸入註冊為 Core 正式指令。LabVIEW 以 `items[{slot, sn}]` 送 SN；Log Solution 監控中或待確認時拒絕。DFU 輸入視窗定位改為只用 checked／unchecked checkbox 模板，自動排序為通道 1～N（N = 設備通道數），淘汰每通道錨點模板與舊 DFU profile 檔。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 02（#3）協定 1.0：envelope 與請求驗證、03（#4）所有模組只認設備 ID

**Status:** ready-for-agent

- [ ] `input` 接受 `items`，slot 範圍 1～N、不可重複，SN 1–64 字元可含逗號冒號；不合法回 `invalid_argument`
- [ ] Log Solution 為 monitoring 或 review 時回 `log_solution_busy`，不做任何 HID
- [ ] 在 DFU 視窗找出全部 checkbox，數量須等於 N，否則回 `hid_failed` 且不輸入任何 SN
- [ ] 排序：依 y 分排（容許約半個 checkbox 高），排內依 x，編號 1～N；與 Log Solution Slot 一致
- [ ] 成功回 `data.entered`
- [ ] 非 DFU 設備回 `unsupported_command`
- [ ] DFU flow 測試以合成畫面覆蓋數量不符、排序、忙碌拒絕；socket 層測試覆蓋參數驗證
- [ ] Pattern 建立流程與模板目錄不再要求每通道錨點模板
