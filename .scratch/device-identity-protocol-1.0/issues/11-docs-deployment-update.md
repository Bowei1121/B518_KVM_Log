# 11: 文件與部署更新

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/12（Parent #1）

**What to build:** 更新 repo 文件以符合協定 1.0 與設備清單，移除舊逗號協定說明，補上部署所需的靜態 IP 規劃與設備清單維護方式。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 04（#5）`check` 1.0：逐格結果、待確認立即回、格數核對、06（#7）DFU `input` 1.0：items、Log Solution 忙碌拒絕、checkbox 自動排序、07（#8）BT `button` start_all 註冊為正式指令

**Status:** ready-for-agent

- [ ] README 的逗號協定範例改為 JSON 1.0，指向 `docs/PROTOCOL.md`
- [ ] config 說明改為設備清單檔，移除舊 DFU profile 檔說明
- [ ] 部署文件含 9 台 JetKVM 與上位機靜態 IP 規劃、EKI-2720G 為非網管交換機的說明、JetKVM 靜態 IP 設定待確認事項
- [ ] `docs/PROTOCOL.md` 與實作一致，第 10 節落差表更新為已完成狀態
