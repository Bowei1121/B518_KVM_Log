# 05: 畫面判讀移出事件迴圈並設單張時間上限

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/6（Parent #1）

**What to build:** `check` 的畫面判讀改在背景執行緒（或程序）執行，單張判讀超過時間上限即放棄該張並記為 unknown，再取下一張。一台設備的慢畫面不會拖慢其他設備或 TCP 回覆。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** 04（#5）`check` 1.0：逐格結果、待確認立即回、格數核對

**Status:** ready-for-agent

- [ ] 判讀不在 asyncio 事件迴圈中同步執行
- [ ] 單張判讀上限可設定（預設約 200 ms），超時記為 `unknown` 並繼續輪詢
- [ ] 測試：一台設備判讀被人為拖慢時，另一台設備的 `ping`／`check` 回覆時間不受影響
- [ ] 既有 `check` 行為與測試不變
