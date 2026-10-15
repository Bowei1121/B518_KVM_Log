# Config

放置 KVM + Log 方案的設定範例。正式憑證、密碼、token、設備私鑰及
現場敏感設定不得提交至 Git。

DFU 的通道數由設備清單 `devices.json`（上位機 `~/Documents/template/devices.json`）的 `channels` 欄位決定，不再使用 `device_profiles.json`。
