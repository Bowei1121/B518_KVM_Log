# 10: 錄影重播壓測工具

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/11（Parent #1）

**What to build:** 提供一個工具，把錄好的 JetKVM 畫面（含時間戳）餵進 KVM Connection Pool 模擬 N 台設備，讓現場用 3 台真機＋6 路模擬做 9 台同時長輪詢的負載驗收。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 05（#6）畫面判讀移出事件迴圈並設單張時間上限

**Status:** ready-for-agent

- [ ] 可指定模擬設備數量與重播畫面來源，模擬設備在 Core 中與真實設備走相同的判讀與長輪詢路徑
- [ ] 可與真實 JetKVM 混用（真實設備由設備清單提供）
- [ ] 記錄 CPU、記憶體、畫面時間戳延遲並輸出報告檔
- [ ] 說明文件描述 30 分鐘驗收程序與判定方式
