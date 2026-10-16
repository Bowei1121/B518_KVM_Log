# 專案摘要 2026-10-16

本摘要整理 2026-10-15 一整天的工作（spec #1「統一設備 ID、設備清單與 LabVIEW TCP JSON 協定 1.0」的實作），供日後查閱。

## 結論

- spec #1 的實作票 #2 到 #12 全部完成，已合併進整合分支 `feat/device-identity-protocol-1.0`（HEAD `305c9b1`），全套測試 334 passed。
- draft PR #16（整合分支 → `main`）已改為 ready for review。尚未合併，也未同步 Gitea 的 `main`。PR 內文的 `Closes #2`～`#12` 會在合併後自動關閉對應 issue。
- 未完成：spec #1 本身（待 LabVIEW 端完成），以及 #13（LabVIEW VI 與 typedef 改版，人工作業）。

## 票與狀態

| 票 | 內容 | 狀態 |
| --- | --- | --- |
| #2 | 設備清單載入與驗證，以設備 ID 取畫面 | 完成 |
| #3 | 協定 1.0 envelope 與請求驗證 | 完成 |
| #4 | 所有模組只認設備 ID | 完成 |
| #5 | check 1.0：逐格結果、待確認立即回、格數核對 | 完成（FCT/BT 仍為模板判讀，見待辦） |
| #6 | 判讀移出事件迴圈，單張時間上限 | 完成（預設 200 ms） |
| #7 | DFU input 1.0：items、忙碌拒絕、checkbox 排序 | 完成 |
| #8 | BT button start_all | 完成 |
| #9 | Monitor UI 改用設備 ID，手動檢查唯讀 | 完成（唯讀以新指令 `inspect` 實作） |
| #10 | Monitor UI 設定頁 | 完成（Tk 版面只做過無頭測試） |
| #11 | 錄影重播壓測工具 | 完成工具與文件；30 分鐘正式驗收尚未執行 |
| #12 | 文件與部署更新 | 完成 |
| #13 | LabVIEW 端改版 | 人工，未開始 |

## 過程中的主要決策

- 設備身份：`devices.json` 為唯一來源（ADR-0007，已改為 Accepted）；協定只帶設備 ID，帶 `kvm_ip` 或 `station` 即回 `forbidden_field`。
- 手動檢查唯讀：使用者選擇新增獨立指令 `inspect`（僅供 Monitor UI，見 `docs/PROTOCOL.md` 5.7）。它不上鎖，也不消耗本輪結果。
- DFU 輸入：通道數取自設備清單，checkbox 只靠勾選／未勾選模板定位，數量須等於通道數。Log Solution 監控中或待確認時拒絕，且不送 HID。
- 判讀執行緒化：DFU 與模板判讀在工作執行緒進行，超時記為 `unknown`，不阻塞其他設備。
- 錄影重播：ReplayKVM 經由與真機相同的 `latest_frame()` 介面提供畫面，模擬設備走真實判讀路徑。

## 程式碼審查（code-review）

- Standards 與 Spec 兩軸各一個子代理審查。
- 修復已在 `fix/review-1` 完成並合併，包括：重啟防護讀錯欄位（`busy` 應為 `is_busy`，已修正並經核對屬實）、`input` 與 `snapshot` 的驗證移到上鎖與 KVM 連線之前、斷線不回覆、錯誤回覆的 `kind` 與文件一致、死碼移除、settings 頁探測結果綁定到列。
- 行為變更：`snapshot` 的 `format` 改為精確比對，`"JPEG"` 現在會被拒絕。

## 待辦與未決

1. ADR-0004 衝突：Monitor UI 直接寫 `devices.json`、直接以 socket 連 KVM、直接呼叫 `core_daemon`，與 `CONTEXT.md` 及 ADR-0004「只透過 TCP 與 Core Service 溝通」不符。需決定補記 ADR 或改架構。
2. `tcp_server.py` 約 1000 行，建議拆出 protocol 模組；命令註冊重複（Repeated Switches／Shotgun Surgery）。審查後決定不併入本輪，留待另案。
3. `_long_poll` 錯誤回覆仍寫 `kind: "unknown"`，與 PROTOCOL 9.7 範例的 `""` 不一致。
4. FCT／BT 的 `check` 仍為模板判讀，尚未改為 Log Solution 顯示契約判讀。
5. STOPPED：人員按停止監控後 Log Solution header 的狀態，待現場確認。
6. JetKVM 正式版是否支援靜態 IP 設定，待現場確認。
7. 30 分鐘錄影重播壓測正式驗收尚未執行。
8. 兩條舊分支 `claude/elated-chaplygin-31da83`、`claude/matt-pocock-skills-setup-b2caf3` 仍在本機與遠端，待確認是否刪除。

## 分支與 worktree

- 遠端只剩 `main`、`feat/device-identity-protocol-1.0`（PR #16 來源）與兩條舊的 `claude/*`。
- 實作用的 `feat/di-*` 與 `fix/review-1` 已於 2026-10-15 結束時刪除。
