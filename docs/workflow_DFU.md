# DFU 設備工作流程

更新日期：2026-09-01（Asia/Taipei）

## 文件定位

本文件是 DFU 設備工作流程的單一來源（source of truth）。日後調整 DFU 操作步驟、slot 規則、影像辨識條件或錯誤處理時，應先修改本文件，再修改程式與驗證案例。

內容來源為前一套 Arduino + Log 方案的 `PROJECT_SUMMARY.md`（最後更新 2026-08-31）與本 JetKVM repo 現行實作。前者是已經 FAE 現場確認的業務流程；後者目前僅實作通用的單筆 SN 輸入與按鈕點擊。

本流程只以 JetKVM 串流畫面判斷狀態並送出 HID 操作；測試機本地 Log 與 CSV 不屬於本 repo 責任。

## 目標流程

### 前置條件

- JetKVM HDMI IN 已連到 DFU 測試機的畫面輸出。
- JetKVM USB HID 已連到 DFU 測試機，鍵盤與滑鼠 report 可正常送達。
- 上位機可連到 JetKVM WebRTC signaling，並能取得即時影格。
- JOB 已提供本輪要啟用的 slot 與對應 SN；SN 數量必須等於啟用 slot 數量。
- 七槽現場版型必須是已確認的 **4 + 3** 排列。只辨識到六槽或版型不完整時，不得繼續輸入。

### 標準操作

1. 取得最新 JetKVM 影格，定位 DFU HMI。
2. 若 HMI 未聚焦，優先用已建立的 Atlas Dock 圖示樣板點擊聚焦；沒有 Dock 樣板時，才使用 slot1 文字附近的安全區域。聚焦後必須重新截圖。
3. 對七槽 profile，先確認 4 + 3 完整版型與 slot1～7 錨點；不使用小型視窗樣板中心直接點擊。
4. 讀取每個 slot checkbox 的實際狀態，只切換與本次 JOB 需求不一致的 slot。不使用 group0 checkbox 做整組快速同步。
5. 再截圖一次，逐槽複驗 checkbox。任一 slot 狀態不符即停止，不得輸入 SN，不得點 OK。
6. 以最後一張複驗截圖重新定位 SN 輸入框與 OK 按鈕。
7. 點擊 SN 輸入框，依 JOB 順序處理每筆 SN：
   1. 輸入一筆完整條碼。
   2. 按一次 Enter。
   3. DFU HMI 會將條碼放入目前啟用 slot，並將焦點移到下一個已勾選 slot。
8. 所有啟用 slot 都填入後，**只點擊一次 OK** 啟動 ATE 測試。Enter 只負責將 SN 放入 slot，不能當作開始測試指令。
9. 回報操作成功，並保留原圖、辨識疊圖、分數與錯誤資訊供追查。

### 四槽相容 profile

- 四槽 `b482_dfu2` 與七槽 `b482_dfu2_7slot` 應是獨立 profile，不可由偵測到的 slot 數量自動猜測。
- 前一方案的上位機正式協定最多傳送 4 slot；若 JetKVM 方案要支援 7 slot，必須另行定義 JOB 格式，不得靜默截斷。

## 狀態與錯誤處理

| 狀態 | 處理 |
|---|---|
| 無 HDMI 影格 | 回報 `no_signal`，不送 HID |
| 找不到 DFU HMI | 嘗試聚焦與重新擷取；仍失敗則回報 `DFU_HMI_NOT_READY` |
| 七槽版型不完整 | 回報 `DFU_HMI_NOT_READY`，不操作 checkbox、SN 或 OK |
| checkbox 不明確 | 記錄 checked／unchecked 分數，重截一次；仍不明確則停止 |
| checkbox 複驗失敗 | 終止本輪，不輸入 SN，不點 OK |
| HID endpoint 錯誤 | 回報輸入失敗，檢查 JetKVM 到 DUT 的 USB 線與主機喚醒狀態 |
| 輸入筆數不符 | 在任何 HID 動作前拒絕 JOB |

## 目前 JetKVM 實作狀態

`host-app/auto_flow.py` 的 `run_flow()` 目前可以：

- 以 `target.png` 定位視窗。
- 以 `Input_target.png` 定位輸入框。
- 點擊輸入框、輸入單筆 SN，並按 Enter。
- 以 `Button_target.png` 定位並點擊按鈕。

尚未實作：

- Repo 內沒有 `host-app/DFU/` pattern 資料夾。
- 沒有 4-slot／7-slot profile、4 + 3 版型驗證與多 SN JOB 格式。
- 沒有逐 slot checkbox 辨識、切換、複驗與聚焦重試。
- 沒有 match session 原圖／疊圖／JSON 診斷存檔。

## 驗收準則

- 4-slot 與 7-slot 使用各自固定的 profile 與現場 pattern，各至少連續 20 輪。
- 驗證全選、稀疏 slot、原本勾選狀態錯亂、HMI 未聚焦與畫面縮放情境。
- 每筆 SN + Enter 必須只填入一個已啟用 slot；所有 SN 填完前不得點 OK。
- 任一辨識或 HID 錯誤都必須 fail closed，不可繼續操作下一步。
- 每輪可由診斷檔案還原使用的影格、命中位置、相似度、HID 動作與結果。
