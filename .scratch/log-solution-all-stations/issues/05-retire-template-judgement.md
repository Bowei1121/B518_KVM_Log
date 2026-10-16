# 05: 淘汰模板判讀結果程式與結果模板

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/26（Parent #21）

**What to build:** 刪除不再使用的模板判讀結果程式與 `testing`／`pass`／`fail` 模板規格，創建 Pattern 不再要求這些圖。操作用模板（各站 Dock 圖示、BT Start All、DFU 視窗／checkbox／OK）保留。

**Blocked by:** 01（#22）FCT／BT 的 check／inspect 改用 Log Solution 判讀

**Status:** ready-for-agent

- [ ] 模板讀數映射為 `check` 結構的程式與模板判讀結果判定移除
- [ ] 模板目錄與創建 Pattern 不再列出 `testing`／`pass`／`fail`；既有檔案存在時不影響運作
- [ ] 操作用模板與流程不變，相關測試仍通過
- [ ] 淘汰路徑的既有測試刪除或改寫，全套測試通過
