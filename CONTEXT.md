# Domain Glossary

## Architecture
- **Core Service**: 獨立運作的 Headless 背景程序（Python Process），負責維護所有 KVM 連線、畫面辨識流水線，並提供 TCP JSON 介面給 LabVIEW。不帶任何 Tkinter GUI 元件。
- **Monitor UI**: 用於觀測畫面、維護 Pattern、手動操作的 Tkinter GUI。它降級為一般的 Client，只透過 TCP 與 Core Service 溝通，不再自己直接去連 WebRTC 或管理 TCP Server。

## Connections & Concurrency
- **KVM Connection Pool**: 在 Core Service 中管理所有 JetKVM 連線的機制。具備自動保活（閒置時定時背景抓圖）與閒置淘汰（例如 10 分鐘無指令即主動斷線釋放資源）功能。
- **Device Lock**: 以設備或 KVM IP 為單位的互斥鎖。確保針對同一台 ATE 的指令被序列化，對於衝突的併發指令採用 Fail-Fast 策略直接回傳 Busy 錯誤。
- **TCP Multiplexing (Per-Device Connection)**: LabVIEW 針對每一台測試機，建立一條專屬且獨立的 TCP Socket 連線到中繼站，確保多機台平行通訊不干擾。

## Protocol & Flow
- **JSON Protocol**: 換行符分隔（Newline-Delimited）的 JSON 格式協定。包含 Request ID，取代原先易碎的逗號分隔純字串協定，無向後相容包袱。
- **Long Polling**: 用於耗時測試的 TCP 等待機制。LabVIEW 對 `check` 送出長 Timeout 請求，Core Service 卡住該 TCP 連線在背景監聽，直到結果出現才回傳，降低頻寬浪費。
- **Frame Freeze Guard**: 畫面凍結防呆機制。強制檢查 JetKVM 底層回傳影格的時間戳 (Presentation Timestamp)，確保測試機 HDMI 畫面活躍，避免因當機誤判前次結果。
