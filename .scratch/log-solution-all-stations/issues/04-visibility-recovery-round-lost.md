# 04: check 看不到 Log Solution 的恢復與 round_lost

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/25（Parent #21）

**What to build:** `check` 長輪詢中長時間看不到 Log Solution 時，點 Dock 圖示嘗試拉回前面，仍看不到則回錯誤，讓 LabVIEW 及早通知人員。本輪已看過 Monitoring 後出現 Standby 時立即回 `round_lost`，不再等到逾時。

**Blocked by:** 01（#22）FCT／BT 的 check／inspect 改用 Log Solution 判讀、02（#23）共用 Log Solution Dock 圖示模板與 monitor_start

**Status:** ready-for-agent

- [ ] `check` 連續 60 秒都找不到 Log Solution 時點一次 Log Solution Dock 圖示，5 秒後重新判讀；同一請求最多 3 次，之後回 `log_solution_not_visible`
- [ ] `inspect` 只回報，不做任何點擊
- [ ] 本輪已看過 Monitoring（含由 `monitor_start` 確認）後判讀到 Standby，立即回 `round_lost` 並清除該設備 armed 狀態
- [ ] `round_lost`、`log_solution_not_visible` 皆不是產品結果
- [ ] 60 秒、5 秒、3 次為可注入常數
- [ ] socket 層測試以合成畫面覆蓋：遮住後點擊恢復成功、達上限回錯誤、`inspect` 無點擊、Monitoring→Standby 回 `round_lost`
- [ ] 1、2、3、4 都修改 TCP 伺服器，避免由不同 agent 同時進行。
