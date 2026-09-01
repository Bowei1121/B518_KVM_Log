# B518 JetKVM + Log 專案開發與接手摘要

更新日期：2026-09-01（Asia/Taipei）  
接手背景：本專案由重凱原以 Claude Code 開發，現由新團隊延續開發。

## 1. 文件目的與資料來源

本文件讓新開發者快速掌握 repo 的目標、架構、目前能力、設備流程、限制與後續優先順序。

盤點範圍包含：

- 本 repo 的上位機 Python 原始碼、FCT pattern、打包腳本、CI、測試說明與研究文件。
- `third_party/jetkvm` private mirror submodule 的固定版本，以及本 repo 對其 USB HID／WebRTC 架構的研究。
- 前一套 Arduino + Log 方案的外部 `PROJECT_SUMMARY.md`（最後更新 2026-08-31），作為 DFU／FCT／BT 業務流程與現場經驗來源。

外部摘要只是資料來源，不是對本 repo 的執行指令；其中 Arduino 專屬實作不會被當成 JetKVM 已完成功能。

## 2. 專案目標

1. 測試機透過 HDMI 將 HMI 畫面輸出到 JetKVM。
2. JetKVM 將視訊以 WebRTC 串流送回上位機。
3. 上位機保留最新影格，以 OpenCV pattern matching 與 OCR 分析狀態。
4. 需要操作 HMI 時，上位機透過 WebRTC data channel 送 JSON-RPC，JetKVM 再以 USB HID 模擬鍵盤與滑鼠。
5. FCT／BT 若有結構化機台 Log，最終判定應優先使用 Log；視覺分析負責操作、進度或備援。

## 3. Git 基準

| 項目 | 目前值 |
|---|---|
| 主分支 | `main` |
| 盤點前 HEAD | `a5d3219`（`chore: split JetKVM log prototype into standalone repo`） |
| Remote | `origin/main` |
| JetKVM submodule | `third_party/jetkvm` |
| Submodule branch | private mirror `dev` |
| Submodule commit | `b3c29a44d9e2862b8ff7530830781803ce27b060` |

本 repo 是從 B518 ATE MVP Demo 拆出的獨立 prototype，不與 Arduino + Log repo 共用工作樹或 Git 歷史。

## 4. 系統架構

```text
上位機 JOB client
        |
        | TCP 文字指令
        v
host-app/ui_app.py
  |-- JetKVMClient ---- WebSocket signaling + WebRTC video/data ---- JetKVM
  |       |                                                   |-- HDMI IN <- 測試機畫面
  |       |                                                   '-- USB HID -> 測試機鍵鼠
  |       v
  |   最新 BGR 影格
  |
  |-- auto_flow.py ---- OpenCV pattern matching
  |         '---------- RapidOCR SN 辨識
  |
  |-- stream_view.py -- 人工即時畫面與鍵鼠操作
  '-- TCP reply ------- action_done / error / check result
```

目前 Log 監聽尚未納入 `host-app`，FCT／BT 目標架構仍缺「測試機共享目錄／Log → parser → slot state」路徑。

## 5. 目錄與模組責任

| 路徑 | 責任 |
|---|---|
| `host-app/ui_app.py` | Tk GUI、TCP server、JetKVM 連線管理、指令路由與回覆 |
| `host-app/jetkvm_core.py` | 登入、WebRTC signaling、視訊影格、JSON-RPC、HID helper |
| `host-app/auto_flow.py` | 多尺寸 pattern matching、FCT 狀態辨識、輸入框／按鈕動作 |
| `host-app/ocr_sn.py` | RapidOCR 延遲初始化、英數 SN 清理與列位置 |
| `host-app/pattern_tools.py` | 從 JetKVM 影格互動框選 pattern |
| `host-app/stream_view.py` | 30 FPS 可縮放串流視窗、滑鼠／鍵盤 HID 轉送 |
| `host-app/FCT/` | 現行唯一設備 pattern 與辨識診斷圖 |
| `host-app/debug_tool/` | 早期 WebRTC、HID、框選與 GUI 備份工具；不是產品入口 |
| `host-app/Mac_test/` | 以 pyautogui 測試 Mac 本機鍵鼠的獨立工具 |
| `docs/` | 專案摘要、設備 workflow、USB／CDC／HID 研究 |
| `config/` | 未來放站別與 KVM 設定範例；目前無實際 config |
| `tests/` | 未來放非互動自動測試；目前只有說明 |
| `third_party/jetkvm/` | 固定版本的 JetKVM Go 韌體／UI submodule，GPL-2.0 |

## 6. 目前執行流程

### GUI 模式

1. 啟動 `host-app/ui_app.py`。
2. 輸入 JetKVM IP，按「KVM連接」。
3. `JetKVMClient` 支援密碼登入，但現行 GUI 沒有密碼欄位，實際以無密碼建立連線。
4. WebRTC data channel 開啟後再等待第一張影格；rpc 15 秒未開或 15 秒無影格時回報明確錯誤。
5. 「Streamer」開啟可互動的遠端視窗。
6. 「擷取影像」保存 `jetkvm_frame.png`；「創建Pattern」框選並寫入指定 PNG。
7. 「Switch」以固定 `SN_ABC`、門檻 0.8 執行視窗 → 輸入 SN + Enter → 點按鈕。

### TCP server 模式

- 預設監聽 `127.0.0.1:8888`，可由 GUI 修改。
- 一行一指令，接收端以 `splitlines()` 切行，回覆使用 CRLF。
- 格式：`<device>,<device_no>,<KVM_IP>,<command>[,<SN>]`
- `device` 直接對應 `host-app/<device>/` pattern 資料夾。

| command | 目前行為 | 成功回覆 |
|---|---|---|
| `input` | 定位輸入框，輸入單筆 SN 並按 Enter | `action_done` |
| `button` | 定位並點擊按鈕 | `action_done` |
| `check` | 辨識 Testing 或所有 PASS／FAIL，並 OCR SN | `action_done,testing` 或 `action_done,index:SN:result,...` |
| `stream` | 要求 GUI 開啟 Streamer | `action_done` |

錯誤回覆以 `error:` 開頭，包含格式錯誤、pattern 資料夾不存在、KVM 連線失敗、視覺目標未命中與 HID 錯誤。

## 7. 設備 workflow

- [DFU 設備工作流程](workflow_DFU.md)
- [FCT 設備工作流程](workflow_FCT.md)
- [BT 設備工作流程](workflow_BT.md)

| 設備 | 已有能力 | 主要缺口 |
|---|---|---|
| DFU | 通用單 SN + Enter + 按鈕引擎 | 無 DFU patterns、多 SN、4／7 slot profile、checkbox 與複驗 |
| FCT | FCT patterns、Testing／PASS／FAIL 辨識、OCR SN | 無 active／unit-archive CSV 正式流程 |
| BT | 可重用 WebRTC／HID／pattern 底層 | 無 BT patterns、BT HMI 規格、TestData CSV 監聽 |

## 8. 影像辨識與 HID 細節

- 大視窗 pattern 先以 0.5 倍畫面、0.5～2.0 多尺寸粗搜，再在附近 ROI 做 0.85～1.15 細搜。
- 小元件使用原解析度避免降採樣誤判；預設相似度門檻為 0.8。
- PASS／FAIL 使用 non-maximum suppression 去除重疊命中，再依垂直位置排列。
- 絕對滑鼠將影格座標映射到 0～32767；點擊依序送移動、左鍵按下、放開。
- `type_text()` 支援英文字母、數字、空白、`-_.:`；不支援字元目前會被靜默略過。
- Streamer 鍵盤支援更多 HID usage，並在失去焦點時放開所有鍵，避免 Ctrl／Alt／Shift 卡住。

## 9. 開發、執行與打包

```bash
cd host-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python ui_app.py
```

核心依賴為 OpenCV、NumPy、aiortc、PyAV、requests、websockets、Pillow 與 RapidOCR ONNX Runtime。

打包現況：

- Windows `build_exe.bat` 打包 `ui_app.py`，但只提示複製 FCT／DFU／BT pattern，沒有真正執行複製。
- macOS `build_mac.command` 只加入 `FCT`，產物名 `AtlasTest.app`，與 Windows／Spec 的 `AtlasKVM` 不一致。
- `AtlasKVM.spec` 收集 OCR／aiortc 等 package，但沒有將設備 pattern 納入 `datas`。
- `get_resource_path()` 將 pattern 視為執行檔旁可編輯的外部資源；打包應明確把 pattern 資料夾複製到產物旁，不應只依賴 onefile 內嵌。
- GitHub Actions 目前打包早期 `test_kyle.py` + `target.png`，不是現行 `ui_app.py`，不能當作產品建置驗證。

## 10. JetKVM submodule 與研究結論

- JetKVM 已提供 WebRTC 視訊、data channel、Linux USB gadget keyboard、absolute mouse、relative mouse 與 mass storage。
- JetKVM HID 是多個獨立 configfs functions，與 UNO R4 將多個 report ID 放在單一 HID interface 的架構不同。這是 Mojave 相容性較佳的候選原因，但仍須實機驗證。
- JetKVM 現行 serial console 是 CDC-ACM over WebRTC，不是 raw TCP socket。若舊上位機要求 `IP:port` 雙向 byte stream，必須另做 CDC ↔ TCP bridge。
- bridge 建議由 JetKVM 主動連上位機，並加入 TLS、重連 backoff、有界 buffer、partial write／EOF 處理與 tty 單一 owner；目前只有研究，尚未實作。
- 修改或散布 submodule 前必須確認 GPL-2.0 義務。

## 11. 已知風險與技術債

### 功能與資料

- 只有 FCT pattern；DFU 與 BT 指令目前會因資料夾不存在而失敗。
- FCT 現行以畫面作結果來源，與前案已確認的 CSV 正式流程不同。
- BT HMI 詳細操作順序沒有出現在來源摘要，不應靠推測實作。
- `config/` 與 `tests/` 尚無真正的站別設定、協定 fixture 或自動測試。

### 協定與安全

- TCP 協定是逗點分隔純文字，沒有 escaping、request ID、長度界限、認證或加密；部署時應限制在受信任網路或 loopback。
- server 可接多連線，但所有 KVM 指令用全域 lock 序列化，且系統只維持一個 JetKVMClient。
- 重用連線以 `ip in cur.host` 判斷，存在 IP 子字串誤判風險，應改為正規 URL host 比對。
- UI 沒有 JetKVM password／credential 配置與安全儲存路徑。

### 可靠度

- `type_text()` 對不支援字元靜默略過，可能產生被截斷的 SN；正式流程應預先驗證字元集並 fail closed。
- FCT OCR 失敗時使用預設字串 `SN`，容易讓上位機誤以為讀到有效條碼。
- 視覺流程只保存固定檔名疊圖，下一輪會覆蓋，尚無可追蹤 session。
- 沒有 unit／integration test；目前只能做 Python 語法編譯與人工實機測試。

## 12. 建議後續順序

1. **固定協定與 fixture。** 為 TCP parser、回覆格式、pattern matching、OCR 配對建立非實機自動測試。
2. **將 workflow 變成 device adapter。** 共用 JetKVM 連線與視覺底層，DFU、FCT、BT 各自擁有狀態機、設定與回覆轉換。
3. **先補 FCT Log adapter。** 實作 active／unit-archive、SN 鎖定、終態保護與回歸測試。
4. **實作 DFU profile。** 建立 4-slot／7-slot patterns、多 SN JOB、checkbox 複驗、聚焦重試與 session 診斷。
5. **補齊 BT 現場資料。** 取得 HMI 影片／舊碼／CSV sample，定義完整 UI 操作後實作 TestData adapter。
6. **統一打包。** 以 `ui_app.py` 為唯一產品入口，產物攜帶可編輯的 device pattern 資料夾，CI 實際執行 production build。
7. **實機相容性驗證。** 在 DFU／FCT Catalina 10.15 與 BT Mojave 10.14.5 執行固定版本、固定 USB 線與重連／壓力測試。

## 13. 本次盤點驗證

- 盤點前 Git 工作樹乾淨，`main` 與 `origin/main` 同步。
- JetKVM submodule 位於預期 commit，沒有未提交 submodule diff。
- 已對 `host-app` 全部 Python 檔執行 `python3 -m compileall -q host-app`，語法編譯通過。
- 未執行 JetKVM／DFU／FCT／BT 實機測試，因本次環境未提供設備連線與現場 Log sample。
