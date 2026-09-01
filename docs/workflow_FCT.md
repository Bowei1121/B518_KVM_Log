# FCT 設備工作流程

更新日期：2026-09-01（Asia/Taipei）

## 文件定位

本文件是 FCT 設備工作流程的單一來源。內容來自前一套 Arduino + Log 方案已完成的現場結論，並與本 JetKVM repo 目前的畫面辨識 prototype 交叉檢查。

FCT 有兩種不同路徑，必須分開看待：

1. **正式目標流程：Log 監聽。** 由 FCT 設備自動偵測 slot，Agent 不截圖、不辨識 checkbox、不送 HID，以 active 與 unit-archive CSV 定案。
2. **目前 JetKVM prototype：畫面辨識。** 以 Testing／PASS／FAIL pattern 與 OCR 讀畫面。此路徑可作為展示或備援，但不可取代 CSV 最終結果。

## 正式目標流程：Log 監聽

### 路徑與 slot 對應

- 使用者必須明確設定兩個路徑，不自動猜測：
  - active 根路徑：`Logs/Atlas/active`
  - 完成檔根路徑：`Logs/Atlas/unit-archive`
- active 的 `group0-slotN` 對應實體 slot N。
- Demo HMI 可顯示 6 slot；舊版正式上位機協定最多 4 slot。擴充協定前不可假設第 5／6 slot 可回傳。

### 啟動與基準快照

1. 驗證 active 與 unit-archive 根路徑存在且可讀取。
2. 啟動本輪監聽前，快照已存在的 archive `records.csv`／`record.csv`，供排除未變動舊資料。
3. 記錄本輪啟動時間。時間戳資料夾可最多早於啟動時間 30 秒，以容納操作人員在測試開始後才啟動 Agent。
4. 開始監聽 `active/group0-slotN` 與完成檔。FCT 流程不操作畫面或 HID。

### active 階段

1. `group0-slotN` 出現後，將實體 slot N 顯示為 `TESTING`。
2. 優先由 active `records.csv` 的以下欄位取得 SN：
   - `MLB_SN`
   - `PrimaryIdentity`
   - `SerialNumber`
3. 只接受完整、合理長度的英數 SN。`NUMBER_SOF0`、純數字年份、測試步驟文字與 `COMPLETING` 不是條碼。
4. `device.log` 只能當 SN 備援來源，不能用來定案 PASS／FAIL。
5. 一旦取得可信 SN，將它鎖定到該 slot 直到本輪結束；active 清空或搬移時不得把 SN 改回「讀取中」。
6. active 長時間未更新時可顯示 `STALLED`，但不在 active 仍存在時自動終止監聽。

### active 結束與 unit-archive 定案

1. active 消失後，已鎖定 SN 的 slot 進入 `COMPLETING`。
2. 只在 `unit-archive/<已鎖定SN>/<時間戳-ID>/system/records.csv` 尋找結果；也相容檔名 `record.csv`。
3. 依時間戳資料夾名稱選擇本輪資料，支援 `_HH-MM-SS` 與 `_H-MM-SS`、毫秒與後綴 ID。不依 Finder Date Modified、CSV mtime 或 CSV 內部時間拒絕結果。
4. 同一 SN 有多筆合格 archive 時，只解析資料夾時間最新的一筆。最新檔案仍在寫入或狀態不明時持續等待，不回退使用舊的重工結果。
5. 解析 status 時忽略 status 空白的軟體／設定 metadata 列，只以 status 非空的測試列判斷。
6. 有任一有效測試列為 FAIL 則定案 FAIL；有效測試列全為 PASS 才定案 PASS；其餘狀況繼續等待或回報 UNKNOWN。
7. 一旦 slot 定案，將其標記為終態。後續遲到的 `TESTING`／`COMPLETING` UI 事件不得覆蓋 PASS／FAIL。
8. active 結束後仍從未取得可信 SN 的 slot，獨立定案為「SN 讀取失敗／FAIL」，不影響其他 slot。

### 逾時原則

- 60 秒未出現任何 active 或新結果：提示「尚未開始」。
- active Log 120 秒未更新：顯示 `STALLED`，但繼續等待。
- 結果總保護逾時建議 900 秒，0 代表不限制。此逾時只用於「尚未出現 active」與「active 全部結束後等待 archive」；active 仍存在時不自動停止。

## 目前 JetKVM 畫面辨識 prototype

`host-app/auto_flow.py` 的 `run_check()` 目前流程為：

1. 用 `host-app/FCT/target.png` 定位 FCT 視窗。
2. 若命中 `Testing_target.png`，回傳 `action_done,testing`。
3. 否則在視窗內找所有 `Pass_target.png` 與 `Fail_target.png`。
4. 將視窗寬度 22%～51% 視為 SN 欄，以 RapidOCR 一次讀取所有列，再依 Y 位置與 PASS／FAIL 配對。
5. TCP `check` 回覆格式為 `action_done,<index>:<SN>:<pass|fail>,...`。

此 prototype 的限制：

- 畫面 OCR 與 pattern 受縮放、字型、視窗位置與畫面更新時序影響。
- 畫面 PASS／FAIL 不是前案已確認的最終可信來源。
- 尚未實作 active／unit-archive 監聽、SN 鎖定、archive 選擇與終態保護。

## 無 SN Log Demo

- 可從新 Log 自動取得 SN 與結果並顯示。
- 啟動前必須建立檔案基準，排除舊資料。
- Demo 只顯示，不回傳 TCP RESULT，不可與正式 JOB 模式混用。

## 驗收準則

- 以現場 active → unit-archive 完整流程驗證 1～6 slot、稀疏 slot、混合 PASS／FAIL 與 SN 讀取失敗。
- 驗證 active 清空後 SN 不倒退，最終結果不被遲到進度事件覆蓋。
- 驗證 status 空白 metadata、單位數小時、毫秒、系統年份不準、CSV mtime 保留與多筆重工 archive。
- 斷言只有已鎖定 SN 且屬於本輪的最新 archive 可以定案。
- 當正式規格啟用 Log 監聽時，測試期間不應有 JetKVM 截圖或 HID 動作。
