# 9. Monitor UI 未來改寫為 C# WPF 與 Core 暫留 Python 的架構邊界

Date: 2026-11-13

## Status
Proposed（純架構決策評估與邊界定義；尚未開立實作票，重寫前優先完成不換語言的 Python 端穩定性改善）

## Context
依據 `docs/PROJECT_SUMMARY_20261108.md` §4，上位機部署於 Windows 11 工廠環境，穩定性優先於效能。當時盤點 `host-app` 約 1.55 萬行 Python，其中 Monitor UI 約 4,400 行；主要依賴為 `aiortc`／`av`、OpenCV、RapidOCR 與 Tkinter，使用 PyInstaller onedir 打包。九台設備的影像比對主要在 OpenCV 的 C++ 層執行，效能不是換語言的理由。

重寫本身帶來現場規則歸零的風險，包括 Frame Freeze Guard 的凍結鎖定與例外處理、斷線恢復、裝置鎖及 HID 交接規則。C# WPF 可改善 Windows 監控介面的開發與維護，但不能以 UI 改寫為由同步重寫已現場驗證的 Core。

ADR-0004 已將 Core Service 與 Monitor UI 的行程及生命週期解耦；ADR-0006 定義長輪詢與設備專屬 TCP 連線，避免等待阻塞其他設備；ADR-0008 將 pop-out 手動 HID 經由 Core 的 TCP 介面處理並遵守自動流程的鎖與交接規則。這些既有邊界與 `docs/PROTOCOL.md` 提供逐步替換 UI 的接縫，不需連帶改動 LabVIEW 產線流程。

## Decision
1. **沿 TCP 接縫逐步替換 Monitor UI**：沿著 ADR-0004／0006／0008 的 TCP JSON-RPC（換行分隔 JSON、JSON-RPC 2.0-like，契約見 `docs/PROTOCOL.md`）採絞殺者模式（strangler fig）。先完成不換語言的 Python 端穩定性改善，再評估以 C#（.NET 10 LTS）WPF 替換 Monitor UI。新 UI 仍是可替換的 TCP client，維持四條獨立 channel：status、stream／`snapshot`、inspect、`hid`；四條 channel 是 client 的連線分工，不是新增協定指令。Core 獨立運作，UI 崩潰、關閉或替換不影響 LabVIEW 產線；UI 不直接接管 WebRTC、辨識流水線、裝置鎖或自動測試流程。
2. **Core Service 暫留 Python**：維持已現場驗證的 WebRTC／`aiortc`、`av`、OpenCV、RapidOCR、Frame Freeze Guard 與裝置鎖狀態機。不採用 Go，因 `gocv` 在 Windows 的建置與維護麻煩；不採用 Rust，因 WebRTC／OpenCV 生態整合與團隊學習成本高。先改善現有實作的穩定性，不以語言重寫替代問題定位。
3. **Core 改寫為 C# 的觸發條件**：僅當出現可歸因於 Python／`aiortc` 本身、且無法修復的現場穩定性問題時，才先進行 SIPSorcery + `SIPSorceryMedia.FFmpeg` PoC，不直接啟動全面重寫。PoC 必須實測長跑穩定性、斷線重連，確認所用版本及相依套件的授權條款，並驗證 RTP timestamp 是否能取代 PTS 供給 Frame Freeze Guard，包括時間戳前進、停滯及換串流的語意。`docs/PROTOCOL.md` 與既有協定測試（例如 `tests/test_core_protocol.py`、`tests/test_protocol_envelope.py`、`tests/test_tcp_server.py`）作為新實作的相容驗收基準；PoC 通過仍須另行開票與決策，才可替換 Core。
4. **工具鏈與授權邊界**：本專案企業內部產線用途，採保守門檻：企業 250+ 台電腦或年營收 100 萬美元以上，不使用 Visual Studio Community 或免費 Community 授權的 C# Dev Kit。採用免費的 .NET 10 SDK／Runtime（MIT）＋ `dotnet` CLI ＋ VS Code 開源 C# 擴充套件（`ms-dotnettools.csharp`，不裝 C# Dev Kit）＋ GitHub Actions；Actions 使用量與費用依組織方案管理，不假設無限免費。需進階 UI 設計器時才另購 Visual Studio Professional。FFmpeg 須使用 LGPL 組態的動態連結 DLL，不混入 GPL／nonfree 組態，並履行授權告知與對應原始碼提供義務。採用版本於實作前再核對授權；依據 [.NET 授權](https://github.com/dotnet/core/blob/main/license-information.md)、[.NET LTS 政策](https://dotnet.microsoft.com/en-us/platform/support/policy)、[Visual Studio Community 企業限制](https://visualstudio.microsoft.com/vs/community/)、[C# Dev Kit 授權說明](https://code.visualstudio.com/docs/csharp/cs-dev-kit-faq)與 [FFmpeg LGPL 指引](https://www.ffmpeg.org/legal.html)。

## Consequences
- **Pros**: 保留已驗證的 Core 與現場規則，將 UI 替換風險限縮在 TCP client；沿既有協定逐步導入 WPF，UI 故障不連帶中斷 LabVIEW 通訊。先改善 Python 穩定性可直接降低現場故障與人工復原需求，避免為效能理由進行無必要的全面重寫。
- **Cons**: 未來須維護 Python 與 C# 兩套工具鏈及跨行程連線；WPF UI 仍需處理重連、長輪詢、影像顯示與手動 HID 釋放。Core 的 Python／原生相依套件部署風險仍存在；SIPSorcery、解碼與時間戳語意尚未驗證，不能先假設 C# Core 較穩定。
- **實作前提與禁止事項**: 本 ADR 為 Proposed，未經開票不啟動實作；Python 穩定性改善也須另行定義範圍及驗收。重寫前優先完成不換語言的 Python 端改善與現場驗證。不得改動 Log Solution 顯示契約或破壞 `docs/PROTOCOL.md` 既有相容性；保留設備識別、錯誤碼、長輪詢、結果與輪次語意、Frame Freeze Guard、裝置鎖及手動 HID 交接行為。不得將 UI 生命週期重新綁定 Core，也不得把重啟視為設備已修復、藉此繞過凍結或人工核對規則。
