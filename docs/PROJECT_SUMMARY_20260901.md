# B518 JetKVM 畫面控制專案開發與接手摘要

更新日期：2026-09-01（Asia/Taipei）  
接手背景：本專案由重凱原以 Claude Code 開發，現由新團隊延續開發。

## 1. 專案責任邊界

本 repo 的唯一運行責任是模仿操作員操作測試機電腦程式：

1. 測試機經 HDMI 將 HMI 畫面輸出到 JetKVM。
2. JetKVM 以 WebRTC 將畫面串流回上位機。
3. 上位機以 pattern matching 與 OCR 判斷畫面狀態及測試結果。
4. 上位機經 JetKVM USB HID 操作測試機的鍵盤與滑鼠。
5. 上位機回覆由畫面辨識得到的 Testing、PASS、FAIL 與操作結果。

測試機本地 Log 的讀取、CSV 解析、資料夾監聽與結果仲裁由另一個獨立專案負責。本 repo 不讀取 Log、不定義 CSV schema、不呼叫該專案，也不等待其結果才回覆視覺判讀。

Repo 名稱保留 JetKVM_Log 以維持既有 Git 與部署識別；名稱不代表本 repo 承擔本地 Log 功能。

## 2. 盤點範圍與 Git 基準

- 盤點了上位機 Python 原始碼、FCT pattern、打包腳本、CI、測試說明與研究文件。
- 前一套 ATE 方案的歷史摘要只用來保留已驗證的 HMI 操作經驗；其 Log 實作不轉移到本 repo。
- 主分支：main。
- 本次文件修正前 HEAD：82880ee。
- JetKVM submodule：third_party/jetkvm，private mirror 的 dev 分支固定於 b3c29a44d9e2862b8ff7530830781803ce27b060。

## 3. 系統架構

    上位機 JOB client
            |
            | TCP 文字指令
            v
    host-app/ui_app.py
      |-- JetKVMClient ---- WebSocket signaling + WebRTC video/data ---- JetKVM
      |       |                                                   |-- HDMI IN <- 測試機畫面
      |       |                                                   '-- USB HID -> 測試機鍵盤／滑鼠
      |       v
      |   最新 BGR 影格
      |
      |-- auto_flow.py ---- OpenCV pattern matching + RapidOCR
      |-- stream_view.py -- 人工即時畫面與鍵鼠操作
      '-- TCP reply ------- action_done / error / 視覺測試結果

UI 顯示的 Log 區是本程式自身的操作與診斷訊息，不是測試機本地 Log。

## 4. 目錄與模組責任

| 路徑 | 責任 |
|---|---|
| host-app/ui_app.py | Tk GUI、TCP server、JetKVM 連線管理、指令路由與回覆 |
| host-app/jetkvm_core.py | 登入、WebRTC signaling、視訊影格、JSON-RPC、HID helper |
| host-app/auto_flow.py | 多尺寸 pattern matching、Testing／PASS／FAIL 辨識、OCR SN、輸入與按鈕操作 |
| host-app/ocr_sn.py | RapidOCR 延遲初始化、英數 SN 清理與列位置 |
| host-app/pattern_tools.py | 從 JetKVM 影格互動框選 pattern |
| host-app/stream_view.py | 可縮放串流視窗、滑鼠／鍵盤 HID 轉送 |
| host-app/FCT | 現行唯一設備 pattern 與辨識疊圖範例 |
| host-app/debug_tool | 早期 WebRTC、HID、框選與 GUI 備份工具；不是產品入口 |
| docs | 專案摘要、設備 workflow、JetKVM 能力研究 |
| config | 未來放站別與 KVM 設定範例；目前無實際 config |
| tests | 未來放非互動自動測試；目前只有說明 |
| third_party/jetkvm | 固定版本的 JetKVM Go 韌體／UI submodule，GPL-2.0 |

## 5. 目前介面與流程

GUI 模式：

1. 啟動 host-app/ui_app.py，輸入 JetKVM IP 並建立連線。
2. WebRTC data channel 開啟後等待第一張影格。
3. Streamer 提供人工確認畫面及遠端鍵鼠操作。
4. 擷取影像與創建 Pattern 用於建立各設備的視覺樣板。
5. Switch 以視窗、輸入框與按鈕 pattern 執行示範流程。

TCP 指令格式為：

    <device>,<device_no>,<KVM_IP>,<command>[,<SN>]

| command | 行為 | 成功回覆 |
|---|---|---|
| input | 定位輸入框，輸入單筆 SN 並按 Enter | action_done |
| button | 定位並點擊按鈕 | action_done |
| check | 辨識 Testing 或所有 PASS／FAIL，並 OCR SN | action_done,testing 或 action_done,index:SN:result,... |
| stream | 要求 GUI 開啟 Streamer | action_done |

check 的結果是本 repo 的正式視覺結果，不與外部 Log 專案合併或仲裁。

## 6. 設備 workflow 與現況

- [DFU 設備工作流程](workflow_DFU.md)
- [FCT 設備工作流程](workflow_FCT.md)
- [BT 設備工作流程](workflow_BT.md)

| 設備 | 已有能力 | 主要缺口 |
|---|---|---|
| DFU | 通用單 SN + Enter + 按鈕引擎 | DFU pattern、多 SN、4／7 slot profile、checkbox 與複驗 |
| FCT | FCT patterns、Testing／PASS／FAIL 辨識、OCR SN | 實機畫面驗證、pattern 耐受度與結果列配對驗證 |
| BT | 可重用 WebRTC、HID、pattern 底層 | BT patterns、BT HMI 操作規格、slot profile 與 Mojave 實機驗證 |

## 7. 已知風險與技術債

- 只有 FCT pattern；DFU 與 BT 指令目前會因資料夾不存在而失敗。
- type_text 對不支援字元會靜默略過；正式流程應先驗證 SN 字元集並 fail closed。
- FCT OCR 未配對時目前會使用 SN 作為預設值；上層必須能辨別此值不是已讀取條碼。
- 視覺流程只保存固定檔名的疊圖，下一輪會覆蓋，尚無可追蹤 session。
- TCP 協定是逗點分隔純文字，沒有 escaping、request ID、長度界限、認證或加密；部署應限制在受信任網路或 loopback。
- 重用 JetKVM 連線以 IP 子字串判斷，存在誤判風險，後續應改為正規 host 比對。
- 打包與 CI 仍以早期腳本為主，尚未一致驗證現行 ui_app 產品入口與外部 pattern 資料夾。

## 8. 建議後續順序

1. 固定 TCP 協定、回覆格式、pattern matching 與 OCR 配對的非實機測試 fixture。
2. 將 DFU、FCT、BT 實作為共用 JetKVM 視覺底層之上的獨立設備 profile 與狀態機。
3. 建立 DFU 的 4-slot／7-slot patterns、多 SN、checkbox 複驗、聚焦重試與 session 診斷。
4. 以現場 FCT 畫面強化 Testing、PASS、FAIL、OCR SN 與多列結果配對。
5. 蒐集 BT HMI 截圖與操作證據，建立 BT patterns、slot profile 與 Mojave 實機驗證。
6. 統一打包，使 ui_app 成為唯一產品入口，並將可編輯的設備 pattern 資料夾放在產物旁。
7. 在 DFU／FCT Catalina 10.15 與 BT Mojave 10.14.5 執行固定版本、固定 USB 線的重連與壓力測試。

## 9. 本次文件修正驗證

- 文件明確將本地 Log、CSV 與跨專案結果仲裁排除於本 repo 範圍外。
- 本次只修改 README 與 docs，不修改 Python、TCP 指令或回覆格式。
- JetKVM／DFU／FCT／BT 實機測試仍需要在有設備與現場畫面的環境執行。
