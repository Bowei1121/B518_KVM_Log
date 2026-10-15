# 01: 設備清單：載入、驗證，並以設備 ID 取畫面

GitHub：https://github.com/Bowei1121/B518_KVM_Log/issues/2（Parent #1）

**What to build:** 新增設備清單模組，讀取上位機設備清單檔（設備 ID → 站別、KVM IP、測試通道數）並驗證。Core Service 缺檔或驗證失敗即拒絕啟動並列出錯誤。TCP JSON 伺服器改為注入設備清單，移除寫死的站別設定。完成後 `snapshot` 只帶設備 ID（例如 `DFU-01`）即可取得對應 JetKVM 的畫面，這是第一條端到端的設備 ID 路徑。

前置：FCT／BT／DFU `check` fail-closed 修正（分支 `claude/elated-chaplygin-31da83`）須先合併 main，本票以合併後版本為基礎。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 設備清單模組對外只有「給路徑 → 回傳設備清單或錯誤列表」，錯誤逐項指出設備 ID／位置與原因
- [ ] 驗證規則：ID 格式 `<站別>-<兩位數>` 且前綴等於站別；ID、IP 不重複；IP 為合法 IPv4；站別限 DFU／FCT／BT；通道數為正整數
- [ ] 缺檔或驗證失敗時 Core 啟動失敗，輸出錯誤列表
- [ ] `snapshot` 依設備 ID 查表連線；帶 `kvm_ip` 或 `station` 回 `forbidden_field`；未知或大小寫不符的 ID 回 `unknown_device`
- [ ] 移除 Core 與舊 DFU profile 檔以外的寫死站別清單（DFU profile 檔的淘汰在 DFU input 票完成）
- [ ] 設備清單模組有暫存目錄 JSON 檔的單元測試（合法、缺檔、每一種錯誤）；`snapshot` 有 socket 層測試（注入設備清單與假 KVM client factory）
- [ ] 提供範例設備清單檔（B518 九台、通道數 4、規劃 IP），不含正式現場設定
