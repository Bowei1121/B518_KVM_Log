# 04: bug：Monitor UI 關閉時進行中的 inspect 有時不會被取消

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/51

**Status:** ready-for-agent（bug）

## What to build

Monitor UI 關閉視窗或切換 Core 連線時，進行中的 `inspect`（手動檢查）應該立即被取消並回傳 `error = "cancelled"`。實際上有時完全不會回傳，要等到 socket timeout（`timeout_sec` + 4 秒）才結束。這會讓關閉 UI 後背景執行緒殘留，也讓 `tests/test_ui_client.py::TestMonitorUIChannels::test_close_cancels_inflight_manual_check_without_blocking` 不穩定。

來源：#20（Monitor UI 通訊分流，PR #29 合併）。

**重現（2026-10-19 本機）**：單獨重複執行上述測試 30 次，main（`3dfe111`）失敗 8 次，PR #50 分支失敗 9 次。失敗時 5 秒內 `manual_check` 完全沒有回傳。全套測試的兩次執行中，有一次就是因為這個測試失敗。

**原因推測（未驗證）**：`host-app/core_channel.py` 的 `abort()` 透過 `_hard_close()` 先 `shutdown(SHUT_RDWR)`，接著立刻 `close()`。這個 socket 設了 timeout，Python 在等待資料時是用 poll 輪詢檔案描述子，而不是直接阻塞在 recv。如果 `close()` 搶在等待的執行緒重新輪詢之前就關掉檔案描述子，那個執行緒可能收不到喚醒訊號，會一直等到 timeout。

**修正方向（建議）**：
- `abort()` 只做 `shutdown` 喚醒等待中的執行緒，不在呼叫端的執行緒 `close`。
- `close` 交給 request 自己的執行緒，在 except 或結束時用 `_drop_locked` 處理。
- 若 request 還沒開始等待（例如還在連線），沿用 generation 計數判斷取消。

## Acceptance criteria

- [ ] 先用測試穩定重現：例如重複執行或加壓，能在修正前穩定失敗。
- [ ] 修正後，關閉 UI 或 `disconnect()` 期間，進行中的 `inspect` 在 1 秒內回 `error = "cancelled"`。
- [ ] 上述測試單獨連續執行 100 次 0 失敗，全套測試連續兩次 0 失敗。
- [ ] `abort()` 仍然不等待 lock（關閉視窗不會被卡住），既有 UI client 測試全部通過。
- [ ] 不要用拉長等待時間的方式讓測試通過。

## Blocked by

None (can start immediately)
