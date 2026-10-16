# 08: 支援 Log Solution 1.2 左定位方塊新位置（新舊幾何並存）

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/32（Parent #21）

**Status:** needs-info

## What to build

Log Solution 1.2 將把左定位方塊移到色帶最左側上方（移除「KVM RESULT」字樣，見 `docs/LOG_SOLUTION_CONTRACT_1.2_PROPOSAL.md` C6）。橋接程式需同時讀得懂 1.1 與 1.2 兩種定位方塊位置（以兩個定位方塊的距離區分：1.1 為 238 單位，1.2 約 320 單位），讓 Log Solution 與橋接程式可分開更新。部署順序：先更新橋接程式，再更新 Log Solution 1.2。

**狀態：等 Log Solution 開發者定案新座標並寫回 `KVM_DISPLAY_CONTRACT.md` 後才能開始。**

## Acceptance criteria

- [ ] 依 Log Solution 契約 1.2 文件的實際座標，判讀同時接受 1.1 與 1.2 幾何，以定位方塊距離自動判斷版本，其餘判讀規則不變
- [ ] 1.1 與 1.2 都能正確判讀四種狀態、1～20 通道、兩排色帶
- [ ] 距離不符兩者的畫面一律 `unknown`（視為不可見）
- [ ] 合成畫面測試覆蓋兩種幾何；若 Log Solution 提供 1.2 實際 Tk 擷取，加入 frame replay 驗證
- [ ] PROTOCOL.md 與部署文件註明支援的契約版本與部署順序

## Blocked by

Log Solution 1.2 新座標定案（外部）。
