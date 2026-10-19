# 09: 釘住 Log Solution 格子顏色（依 1.2 建議 C7）

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/34（Parent #21）

**Status:** ready-for-agent

## What to build

橋接程式判讀 Log Solution 色帶時用的格子顏色，目前是自己從 Log Solution 程式抄來的，沒有測試保護；NOTEST 還和 Log Solution 差一個色階。依 `docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md` C7 的顏色表，把橋接的顏色值釘住，任何一邊改了顏色都會被測試抓到；顏色表以外的顏色一律不得判成產品結果。

## Acceptance criteria

- [ ] 橋接的格子顏色與 C7 顏色表逐項相同：PASS `#00ef00`、FAIL `#ff0000`、NOTEST `#f04bf1`、TIMEOUT `#ff9900`、STOPPED `#bfbfbf`、TESTING `#ffff00`、WAITING `#d9d9d9`、COMPLETING `#82c7ff`；通道數以外的格子為黑色
- [ ] NOTEST 改為 `#f04bf1`（原為 `#f04bf0`）
- [ ] 不認得 `STALLED`；`#ff9900` 判為 TIMEOUT
- [ ] 測試：釘住上表每一個值（以十六進位寫在測試裡，不從程式常數讀回來比對自己）
- [ ] 測試：任兩種顏色的 RGB 歐氏距離大於 2 × 容許誤差（目前 20），確保判定範圍不重疊
- [ ] 測試：合成畫面中某格塗上顏色表以外的顏色（例如 `#8040c0`），`check` 不得回 `taken`，該格不得成為產品結果
- [ ] 既有測試全部通過（以本機權限執行）

## Blocked by

None (can start immediately)
