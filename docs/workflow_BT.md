# BT 設備工作流程

更新日期：2026-09-01（Asia/Taipei）

## 文件定位

本文件是 BT 設備工作流程的單一來源。內容來自前一套 Arduino + Log 方案的現場摘要，並與本 JetKVM repo 現況比對。

來源文件已明確定義 BT 結果 Log、slot 對應與舊 macOS 條件，但沒有完整記錄 BT HMI 的每個點擊與輸入元件。本文件不猜測未被記錄的操作；該部分必須用現場 HMI、舊版程式或影片補齊後再定稿。

## 已確認條件

- BT 現場主機為 macOS Mojave 10.14.5。
- Demo 視窗使用 4 slot，與舊版正式協定最多 4 slot 一致。
- BT 結果來源為 `TestData/YYYY-MM-DD/PASSED|FAILED/*.csv`。
- `Thread0`～`Thread3` 固定對應 slot1～slot4。
- 無 SN Log Demo 只顯示，不回傳 TCP RESULT。
- 正式 BT 過去需要影像定位與 HID，過程中會暫時隱藏 Agent 視窗，避免遮蓋或干擾測試 HMI。

## 目標流程

### 前置條件

- JetKVM HDMI IN 與 USB HID 已連到 BT Mac，上位機可收到影格並送出鍵鼠 report。
- JOB 提供 1～4 個 slot 與 SN，且對應關係無歧義。
- 使用者已明確選擇 BT `TestData` 根路徑。
- 現場使用的 JetKVM 鍵盤、絕對滑鼠、相對滑鼠必須在 Mojave 10.14.5 逐項實機驗證，不可只以 Catalina 或新版 macOS 結果代替。

### JOB 啟動階段

1. 接收並驗證 JOB，確認 slot 範圍、SN 數量與重複值策略。
2. 在任何輸入前，快照當日 `PASSED`／`FAILED` 內現有 CSV 的路徑、大小、修改時間與可用識別資訊，用來排除舊測試。
3. 暫時隱藏或移開 Agent 視窗，取得無遮蓋的 BT HMI 影格。
4. 依 BT profile 定位 HMI，處理 slot、SN 輸入與開始動作。
5. 只有當所有必要元件都命中、slot 狀態驗證通過、HID 沒有回報錯誤時，才進入 Log 監聽。

> 待補規格：BT HMI 的 slot 選擇規則、SN 輸入順序、Enter 行為、開始按鈕與需要的 pattern 名稱，尚未在來源文件中完整定義。

### CSV 結果監聽

1. 監聽當日目錄：
   - `TestData/YYYY-MM-DD/PASSED/*.csv`
   - `TestData/YYYY-MM-DD/FAILED/*.csv`
2. 只處理啟動快照後新增，或啟動後內容確實變動且已穩定的檔案。
3. 由 CSV 內的 Thread 識別 slot：`Thread0` → slot1，`Thread1` → slot2，`Thread2` → slot3，`Thread3` → slot4。
4. 由 CSV 內容取得 SN 並與 JOB 的 slot／SN 對應交叉檢查。若檔案所在目錄與 CSV 內狀態矛盾，不靜默選擇其一，應回報資料不一致。
5. `PASSED` 定案 PASS，`FAILED` 定案 FAIL。每個 slot 只能定案一次，遲到的舊事件不得覆蓋。
6. 所有要求 slot 定案後，整理為上位機 RESULT；缺少 slot、無法配對 SN 或逾時必須明確回報。

### 無 SN Log Demo

1. 由操作人員啟動 Demo，啟動前建立 CSV 基準快照。
2. 只從新 Log 取得 SN、Thread／slot 與 PASS／FAIL。
3. 在 Demo UI 顯示結果，不送出正式 TCP RESULT。

## 錯誤處理

| 錯誤 | 處理 |
|---|---|
| Mojave 無法穩定接收 HID report | 停止自動操作，保留 JetKVM USB descriptor／endpoint 診斷，不假設動作已完成 |
| 找不到 BT HMI 或 pattern 低於門檻 | 不送後續 HID，保留原圖與疊圖 |
| `TestData` 路徑不存在／不可讀 | 在測試啟動前拒絕 JOB |
| Thread 不在 0～3 | 標記為未知 slot，不納入正式結果 |
| CSV SN 與 JOB SN 不符 | 回報配對錯誤，不將結果套給其他 slot |
| 同一 slot 出現矛盾 PASS／FAIL | 保留全部證據並回報衝突，不靜默覆蓋 |

## 目前 JetKVM 實作狀態

- Repo 內沒有 `host-app/BT/` pattern 資料夾。
- `ui_app.py` 的 TCP parser 會將設備名稱當作 pattern 資料夾，因此傳入 `BT` 目前會回覆 `pattern folder not found (BT)`。
- 通用 `run_flow()` 只支援單筆 SN 輸入、Enter 與單一按鈕，沒有 BT 特定的 slot 與多 SN 邏輯。
- 尚未實作 `TestData` CSV 監聽、Thread 配對、啟動快照、終態保護與正式 RESULT 組裝。

## 驗收準則

- 在 Mojave 10.14.5 實機驗證鍵盤、絕對滑鼠、相對滑鼠，包含長時間與重連測試。
- 驗證 1～4 slot、稀疏 slot、不同 Thread 完成順序、混合 PASS／FAIL 與同 SN 重工。
- 驗證啟動前舊 CSV 不會被誤配到新 JOB。
- 每個 Thread 只能套用到固定 slot，CSV SN 必須與 JOB SN 一致。
- 為未定義的 BT HMI 操作補齊現場證據後，再將「待補規格」改成可自動驗收的明確步驟。
