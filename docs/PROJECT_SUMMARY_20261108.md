# 專案摘要 2026-11-08：#193 合併、ATE PR #145 跨 repo 審查、C#／Rust 語言評估

接續 `PROJECT_SUMMARY_20261107.md`。

## 1. 11-07 摘要之後合併到本 repo 的內容

| PR／提交 | 內容 |
| --- | --- |
| #191 | check 期間可手動 HID（復盤：`docs/retros/2026-11-07-pr191-check期間手動HID.md`） |
| #192 | 比對診斷圖歷史歸檔（復盤：`docs/retros/2026-11-07-比對診斷圖歷史歸檔.md`） |
| #193（`df1b5f3`） | JetKVM 影格改用 BT.601 解碼；替 Log Solution 讀取存下標註診斷圖，加上 LS 定位配對過程與最高 4 倍放大圖 |

GitHub `main` 在 `df1b5f3`。Gitea `origin/main` 原本停在 `2e12906`（少 #193），這次隨本摘要一起補推。

## 2. 跨 repo 審查：`Bowei1121/B518_205_207_ATE` PR #145

PR 內容：Log Solution 設定頁在 macOS 改用 `osascript choose folder ... invisibles true`，讓 KVM 只用滑鼠也能選到 `.` 開頭的隱藏資料夾；工站啟動時路徑錯誤會顯示「專案 / 機型：欄位（路徑值或未設定）」。

審查格式沿用本 repo PR #178：範圍、驗證、必須修改、建議，最後附結論表（審查版本／結論／阻擋／驗證），並依 ATE repo 的 `skills/issue-pr-review-loop/references/workflow.md`。

| 輪次 | head | 結論 | 重點 |
| --- | --- | --- | --- |
| 初審（Review 5813381017） | `66a1128` | 需修改 | ① `subprocess.run` 同步等 osascript，Tk 主迴圈停住，KVM 心跳凍結，所有 `_schedule_ui` 計時器停擺，輪次中也能開設定；② darwin 分支的測試全被 patch 掉 |
| 再審（Review 5813485685） | `bf198d0` | 審查通過 | 改成 `Popen` 加 `_schedule_ui` 100 ms 輪詢；關設定窗、root destroy、`close()` 時 terminate；防連點；去掉結尾 `/`；新測試鎖住心跳持續跳動、成功、取消、兩種 fallback、關窗取消 |

仍未處理的非阻擋建議：
- osascript 對話框可能在 App 後面（加 `activate`／`default location`），連點時沒有任何提示。
- `stderr=DEVNULL`，fallback 時沒有記錄。
- 錯誤訊息沒列出原因，`repr()` 會露出引號。
- 測試名稱與實際驗證的內容不符；optional 欄位的錯誤訊息沒有測試。
- PR 沒有 `Refs #`，修正者也沒有逐項回覆。

限制：審查 session 的本機 pytest 被權限擋下，只能靠讀程式加上 CI `test-and-package` pass；osascript 實際顯示與實機 DFU／KVM 都還沒驗證。合併需使用者授權，並以 `--match-head-commit bf198d0…` 綁定版本。

## 3. 待辦

- ATE #145：等使用者授權合併；合併後實機確認對話框是否在前景，以及選取期間 JetKVM 端的心跳是否持續跳動。
- 本 repo 延續 11-07 的待辦：11-09 客戶看線（BT 測試、DFU 條碼），JetKVM 串流品質調最高，觀察 `core_status.readable_ratio`。

## 4. 未來改用 C#／Rust 的語言評估（純討論）

11-07～11-08 的另一個 session 只做討論，沒有改程式，也沒有開票。

### 4.1 前提（使用者提供）

- 上位機是 **Windows 11**，工廠環境。
- 優先順序：**穩定性第一，效能第二**。
- 現狀：Python 約 1.55 萬行（`host-app`），其中 Monitor UI 約 4,400 行（`ui_app.py` 2,177 行，加上 widgets、stream_view、設定頁等）。主要依賴有 `aiortc`／`av`（WebRTC＋H.264）、`opencv-python`、`rapidocr_onnxruntime`、Tkinter，用 PyInstaller onedir 打包。

### 4.2 結論

| 語言 | 評價 | 理由 |
| --- | --- | --- |
| **C# / .NET 10 LTS** | **首選**（使用者傾向） | WPF 遠比 Tk 好；原生支援 Windows Service、Event Log、`dotnet-dump`；self-contained 單一 exe 部署。唯一的大風險是 WebRTC 函式庫 SIPSorcery |
| Python（維持現狀） | 可接受 | 有現場驗證。弱點：asyncio task 例外可能默默死掉、PyInstaller、`av` 記憶體 |
| Go | 備胎（只適合 Core） | JetKVM 本身用 Go＋pion，協定相容性最穩；但 `gocv` 在 Windows 上 build 很麻煩，UI 也弱 |
| Rust | 不採用 | WebRTC 與 OpenCV 的生態系不成熟，抵掉語言本身的安全優勢；團隊學習成本高 |

- **效能不是換語言的理由**：九台設備，比對都在 OpenCV 的 C++ 裡跑。
- **重寫本身就是最大的穩定性風險**：現有版本累積的現場規則（例如 freeze latch 的例外）都會被歸零。

### 4.3 建議路線（strangler fig，沿著 ADR-0004 的 TCP 接縫）

1. **先做不需要換語言的穩定性改善**（候選，尚未開票）：
   - 停用 `host-app/AtlasKVM.spec` 的 UPX 壓縮，避免防毒誤判；現由 #202 實作，Monitor UI、Core 與共用收集目錄皆設定 `upx=False`。
   - Core 用 WinSW／NSSM 包成 Windows Service，搭配 `sc.exe failure` 自動重啟（`manage_core.bat` 目前沒有看門狗）。
   - OS 端：考慮 Win11 IoT Enterprise LTSC 2024；控管 Windows Update；網卡關閉省電；設定高效能電源計畫；Defender 排除 `dist\AtlasKVM\` 與 template 目錄。
   - Core 的 `create_task` 統一加上 done callback，task 例外時寫 log 並 `os._exit(1)` 交給服務重啟（fail-fast＋supervisor）。
2. **用 C# WPF 重寫 Monitor UI**，Core 維持 Python。UI 只是 TCP client（四條 channel：status、stream／`snapshot`、inspect、`hid`），壞了不影響 LabVIEW 產線。
3. **Core 改寫要有條件**：出現 Python／aiortc 本身造成、而且修不掉的現場問題時，才做 SIPSorcery PoC。PoC 要測長時間執行、斷線重連，以及 **RTP timestamp 能不能取代 PTS 給 Frame Freeze Guard 用**。
4. `docs/PROTOCOL.md` 就是合約；現有的協定測試要能拿來驗證新的實作。

### 4.4 SIPSorcery 重點

- 純 C# 的 WebRTC 實作（SDP、ICE、DTLS、SRTP、SCTP DataChannel），主要由一個人維護。
- 不負責解碼；H.264 解碼要另外用 `SIPSorceryMedia.FFmpeg`（跟現在 `av` 一樣，底層都是 FFmpeg）。
- 可以跟 `jetkvm_core.py:168-236` 一對一對照：`rpc` DataChannel、`recvonly` H.264、WebSocket signaling `/webrtc/signaling/client`。JetKVM 端不用改。
- 待確認：授權近年可能有調整（推測），導入前要看 LICENSE。

### 4.5 費用

- C#、.NET SDK／Runtime、WPF：免費（MIT），產線部署不用授權費。
- **Visual Studio Community 不能用**：公司超過 250 台電腦或年營收超過 100 萬美元就算企業。**C# Dev Kit 也套用同一條規定。**
- 零成本的路線：`dotnet` CLI＋VS Code 開源 C# 擴充套件（不裝 Dev Kit）＋GitHub Actions。需要 UI 設計器或進階除錯的人，再買 VS Professional（約 USD 45／人／月，以官網為準）。
- 其他授權：OpenCvSharp4 是 Apache 2.0、ONNX Runtime 是 MIT；FFmpeg 要用 LGPL 版本並以 DLL 動態連結。

### 4.6 尚未決定／後續

- 使用者傾向 C#，但目前只是討論，**沒有開票，也還沒寫 ADR**。
- 上面 4.3 第 1 點的穩定性改善，使用者選擇先不開票。

## PR #194：Dock 點擊後移開滑鼠、等新影格、重試一次

- 問題：BT 點 Dock 後滑鼠停在圖示上，程式名稱提示蓋住 Start All；另外 `session.frame()` 可能拿到點擊前的舊影格。
- 做法：`HidSession` 新增 `park_pointer()`、`wait_fresh_frame()`、`match_with_retry()`、`focus_dock_fresh()`（Dock 點擊 → 停放 → 等新影格的唯一入口）。BT／DFU／monitor 都走它；BT 與 DFU 比對未命中時，不再點 Dock，移開滑鼠、等新影格後重比一次（key 加 `_retry`）。Start All／OK 點完也移開滑鼠。
- 設定：`~/Documents/template/COMMON/hid_settings.json`，`pointer_park: [x, y]`（預設螢幕上緣中間；開自動隱藏選單列時要改）、`fresh_frame_timeout_sec`（預設 2）。每個指令讀驗一次，在第一次 HID 前驗證，`null` 與錯型別回 `hid_failed`。
- 審查第一輪（codex）4 項修正：(1) 移開滑鼠為 best effort，失敗只記 `park_failed`，不把已成功的點擊報成失敗；(2) 設定先驗證；(3) 以停放時最新影格序號為基準，settle 期間到達的影格也算新影格；(4) `monitor_start` 的等新影格併入點擊起算的 confirm 期限（PROTOCOL §5.6 的 3 秒）。
- 教訓：Windows 的 `time.monotonic()` 解析度約 15 ms，測試中比較「收件時間 > 停放時間」要用明確差值，不能靠連續呼叫。
- 待現場驗證：提示是否真的不再蓋住 Start All；`pointer_park` 在現場的最佳位置。

- 複審 2 項修正：移開滑鼠改走 `JetKVMClient.park()`（manual RPC id 空間，延遲錯誤不污染點擊的 `rpc_errors`，也不再清共享佇列）；`monitor_start` 的 confirm 改用從 Dock 點擊起算的絕對 deadline，讀取前與接受前都檢查。
