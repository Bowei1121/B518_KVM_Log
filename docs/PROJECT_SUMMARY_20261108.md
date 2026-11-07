# 專案摘要 2026-11-08：未來改用 C#／Rust 的語言評估（純討論）

接續 `PROJECT_SUMMARY_20261107.md`。11-07～11-08 的 session 只做討論，沒有改程式、沒有開票。

## 1. 前提（使用者提供）

- 上位機是 **Windows 11**，工廠環境。
- 優先順序：**穩定性第一，效能第二**。
- 現狀：Python 約 1.55 萬行（`host-app`），其中 Monitor UI 約 4,400 行（`ui_app.py` 2,177 行，加上 widgets、stream_view、設定頁等）。主要依賴有 `aiortc`／`av`（WebRTC＋H.264）、`opencv-python`、`rapidocr_onnxruntime`、Tkinter，用 PyInstaller onedir 打包。

## 2. 結論

| 語言 | 評價 | 理由 |
| --- | --- | --- |
| **C# / .NET 10 LTS** | **首選**（使用者傾向） | WPF 遠比 Tk 好；原生支援 Windows Service、Event Log、`dotnet-dump`；self-contained 單一 exe 部署。唯一的大風險是 WebRTC 函式庫 SIPSorcery |
| Python（維持現狀） | 可接受 | 有現場驗證。弱點：asyncio task 例外可能默默死掉、PyInstaller、`av` 記憶體 |
| Go | 備胎（只適合 Core） | JetKVM 本身用 Go＋pion，協定相容性最穩；但 `gocv` 在 Windows 上 build 很麻煩，UI 也弱 |
| Rust | 不採用 | WebRTC 與 OpenCV 的生態系不成熟，抵掉語言本身的安全優勢；團隊學習成本高 |

- **效能不是換語言的理由**：九台設備，比對都在 OpenCV 的 C++ 裡跑。
- **重寫本身就是最大的穩定性風險**：現有版本累積的現場規則（例如 freeze latch 的例外）都會被歸零。

## 3. 建議路線（strangler fig，沿著 ADR-0004 的 TCP 接縫）

1. **先做不需要換語言的穩定性改善**（候選，尚未開票）：
   - `host-app/AtlasKVM.spec` 第 45、62、70 行的 `upx=True` 改成 `False`，避免防毒誤判。
   - Core 用 WinSW／NSSM 包成 Windows Service，搭配 `sc.exe failure` 自動重啟（`manage_core.bat` 目前沒有看門狗）。
   - OS 端：考慮 Win11 IoT Enterprise LTSC 2024；控管 Windows Update；網卡關閉省電；設定高效能電源計畫；Defender 排除 `dist\AtlasKVM\` 與 template 目錄。
   - Core 的 `create_task` 統一加上 done callback，task 例外時寫 log 並 `os._exit(1)` 交給服務重啟（fail-fast＋supervisor）。
2. **用 C# WPF 重寫 Monitor UI**，Core 維持 Python。UI 只是 TCP client（四條 channel：status、stream／`snapshot`、inspect、`hid`），壞了不影響 LabVIEW 產線。
3. **Core 改寫要有條件**：出現 Python／aiortc 本身造成、而且修不掉的現場問題時，才做 SIPSorcery PoC。PoC 要測長時間執行、斷線重連，以及 **RTP timestamp 能不能取代 PTS 給 Frame Freeze Guard 用**。
4. `docs/PROTOCOL.md` 就是合約；現有的協定測試要能拿來驗證新的實作。

## 4. SIPSorcery 重點

- 純 C# 的 WebRTC 實作（SDP、ICE、DTLS、SRTP、SCTP DataChannel），主要由一個人維護。
- 不負責解碼；H.264 解碼要另外用 `SIPSorceryMedia.FFmpeg`（跟現在 `av` 一樣，底層都是 FFmpeg）。
- 可以跟 `jetkvm_core.py:168-236` 一對一對照：`rpc` DataChannel、`recvonly` H.264、WebSocket signaling `/webrtc/signaling/client`。JetKVM 端不用改。
- 待確認：授權近年可能有調整（推測），導入前要看 LICENSE。

## 5. 費用

- C#、.NET SDK／Runtime、WPF：免費（MIT），產線部署不用授權費。
- **Visual Studio Community 不能用**：公司超過 250 台電腦或年營收超過 100 萬美元就算企業。**C# Dev Kit 也套用同一條規定。**
- 零成本的路線：`dotnet` CLI＋VS Code 開源 C# 擴充套件（不裝 Dev Kit）＋GitHub Actions。需要 UI 設計器或進階除錯的人，再買 VS Professional（約 USD 45／人／月，以官網為準）。
- 其他授權：OpenCvSharp4 是 Apache 2.0、ONNX Runtime 是 MIT；FFmpeg 要用 LGPL 版本並以 DLL 動態連結。

## 6. 尚未決定／後續

- 使用者傾向 C#，但目前只是討論，**沒有開票，也還沒寫 ADR**。
- 上面第 3 節第 1 點的穩定性改善，使用者選擇先不開票。
