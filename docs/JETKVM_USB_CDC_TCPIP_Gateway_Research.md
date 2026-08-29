# JetKVM 作為 USB CDC-ACM → TCP/IP 網關之可行性研究

> 研究日期：2026-08-28（Asia/Taipei）
>
> 研究基準：`jetkvm/kvm` `dev` 分支，commit [`b3c29a44d9e2862b8ff7530830781803ce27b060`](https://github.com/jetkvm/kvm/commit/b3c29a44d9e2862b8ff7530830781803ce27b060)
>
> 本地來源：`../third_party/jetkvm` submodule，固定於上述 commit；其 `origin` 為公司 private mirror，`upstream` 為 `https://github.com/jetkvm/kvm.git`。
>
> 研究範圍：JetKVM 是否能讓「測試電腦（USB Host）」把資料送入 USB CDC-ACM 虛擬序列埠，再由 JetKVM 經乙太網路以 TCP/IP 傳給上位機。

## 結論摘要

**有機會，而且可行性高；現有專案已完成最困難的 USB CDC-ACM 與雙向資料路徑，但尚未直接提供 raw TCP client/server 網關。**

目前程式已經可以：

```text
測試電腦的 COM / ttyACM*
        ⇅ USB CDC-ACM
JetKVM 的 /dev/ttyGS0
        ⇅ 現有 Go bridge
WebRTC DataChannel（cdcacm）
        ⇅
上位機瀏覽器的 USB Serial Console
```

使用者要求的嚴格目標則是：

```text
測試電腦的 COM / ttyACM*
        ⇅ USB CDC-ACM
JetKVM 的 /dev/ttyGS0
        ⇅ 新增的 bridge service
TCP（建議 TLS）經 JetKVM RJ45
        ⇅
上位機程式
```

兩者只差最後一段傳輸與其生命週期、安全、斷線重連及流量控制。現有 `cdc_acm_console.go` 已證明應用程式可直接開啟 `/dev/ttyGS0` 並雙向搬移任意 byte；改成或另加 `net.Dial` / `net.Listen` 的 TCP 搬運器，不需要更換 JetKVM 硬體，也不需要重新發明 USB gadget。合理的工程判斷是：**先做獨立 PoC，成功機率高；若要量產或長時間 ATE 使用，仍需補齊可靠性與資安設計。**

## 名詞與需求邊界

本報告把「USB CDC」解讀為 **CDC-ACM 虛擬序列埠**。這與 CDC-NCM 不同：

- CDC-ACM：測試電腦看到 COM port（Windows）或 `/dev/ttyACM*`（Linux），符合「CDC 轉 TCP」的描述。
- CDC-NCM：測試電腦看到 USB 網卡，本身已是 IP link，不是序列資料轉換。JetKVM 有一個尚未合併的 [CDC-NCM PR #1470](https://github.com/jetkvm/kvm/pull/1470)，但在本次固定的 commit 中沒有 NCM 實作，因此不能列為現況支援；而且它解決的是不同問題。

## 現況直接支援

| 能力 | 判定 | 原始證據 |
|---|---|---|
| JetKVM 的 USB-C 連到目標／測試電腦，RJ45 連網 | 已支援 | JetKVM 官方 [Quick Start](https://jetkvm.com/docs/getting-started/quick-start) 明確要求 USB-C 接目標電腦、Ethernet 接網路。 |
| 讓測試電腦枚舉 CDC-ACM serial device | 已支援，預設關閉 | `serial_console` 是可切換的 USB gadget function；其 configfs function 為 `acm.usb0`：[本地 `internal/usbgadget/serial_console.go` L3-L8](../third_party/jetkvm/internal/usbgadget/serial_console.go#L3-L8)、[固定 commit](https://github.com/jetkvm/kvm/blob/b3c29a44d9e2862b8ff7530830781803ce27b060/internal/usbgadget/serial_console.go#L3-L8)。預設組態將它設為 `false`：[本地 `ui/src/components/UsbDeviceSetting.tsx` L32-L39](../third_party/jetkvm/ui/src/components/UsbDeviceSetting.tsx#L32-L39)。 |
| JetKVM Linux 端取得 `/dev/ttyGS0` | 已支援 | 後端固定使用 `/dev/ttyGS0`：[本地 `cdc_acm_console.go` L10-L20](../third_party/jetkvm/cdc_acm_console.go#L10-L20)。官方硬體 E2E 測試也檢查啟用後目標端出現 `ttyACM`、JetKVM 端出現 `/dev/ttyGS0`：[本地 `ra-all.spec.ts` L2618-L2643](../third_party/jetkvm/ui/e2e/remote-agent/ra-all.spec.ts#L2618-L2643)。 |
| CDC byte 雙向搬移到遠端 | 已支援，但傳輸是 WebRTC DataChannel | 程式從 `/dev/ttyGS0` read 後 `d.Send`，並把 DataChannel message write 回 tty：[本地 `cdc_acm_console.go` L26-L53](../third_party/jetkvm/cdc_acm_console.go#L26-L53)。`webrtc.go` 將 `cdcacm` channel 路由到此 handler：[本地 `webrtc.go` L573-L579](../third_party/jetkvm/webrtc.go#L573-L579)。 |
| 上位機瀏覽器的 serial console UI | 已支援 | UI 建立 `cdcacm` DataChannel 並渲染 USB Serial Console：[本地 `devices.$id.tsx` L941-L949](../third_party/jetkvm/ui/src/routes/devices.$id.tsx#L941-L949)、[L1142-L1148](../third_party/jetkvm/ui/src/routes/devices.$id.tsx#L1142-L1148)。 |
| 嚴格的 TCP socket bridge（TCP server 或 TCP client） | **未直接支援** | 在固定 commit 中，CDC handler 只依賴 `os.File` 與 `webrtc.DataChannel`，沒有 `net.Listen` / `net.Dial`：[本地 `cdc_acm_console.go` L1-L65](../third_party/jetkvm/cdc_acm_console.go#L1-L65)。全 repo 搜尋也未找到把 `/dev/ttyGS0` 接到 TCP socket 的實作。 |

這項 CDC-ACM 能力不是推測中的草案。JetKVM 官方已於 2026-03-27 將 [PR #1352](https://github.com/jetkvm/kvm/pull/1352) 合併至 `dev`；PR 摘要明載 `acm.usb0`、`/dev/ttyGS0`、`cdcacm` WebRTC channel、預設關閉及雙向測試計畫。對應 merge commit 是 [`edaa86c0d3b360fa1dde1cd161406766bc84fd39`](https://github.com/jetkvm/kvm/commit/edaa86c0d3b360fa1dde1cd161406766bc84fd39)。目前 repo 的 E2E 更進一步自動化驗證 host→browser 與 browser→host 的資料路徑：[本地 `ra-all.spec.ts` L2662-L2718](../third_party/jetkvm/ui/e2e/remote-agent/ra-all.spec.ts#L2662-L2718)。

### 為何現有 WebRTC 不能直接稱作 TCP

現有功能已能「透過 IP 到上位機」，但不等於 raw TCP socket。IETF [RFC 8831](https://www.rfc-editor.org/rfc/rfc8831.html) 定義 WebRTC DataChannel 的典型 protocol stack 為 SCTP over DTLS over ICE/UDP；相對地，[RFC 9293](https://www.rfc-editor.org/rfc/rfc9293.html) 定義 TCP 為可靠、依序的 byte-stream。兩者都能可靠傳資料，但 wire protocol、連線 API、訊息／byte-stream 邊界及上位機整合方式不同。因此：

- 若「上位機用 JetKVM 網頁收資料」可接受，現況可能已滿足大部分需求。
- 若上位機既有軟體要求 `IP:port` TCP stream，必須新增 TCP bridge。

## 硬體與韌體可行性

### 1. USB device mode 與 Linux gadget 路徑已在產品上運作

JetKVM 現有程式透過 Linux configfs 組合 keyboard、mouse、audio、mass storage 與 CDC-ACM：[本地 `internal/usbgadget/config.go` L54-L88](../third_party/jetkvm/internal/usbgadget/config.go#L54-L88)。Linux 核心官方 [USB gadget configfs 文件](https://docs.kernel.org/usb/gadget_configfs.html) 說明：具有 UDC 的 Linux 裝置可透過 configfs 建立多功能 USB gadget；官方 [Gadget Testing](https://docs.kernel.org/usb/gadget-testing.html#testing-the-acm-function) 則明載 ACM function 名稱是 `acm`，host 使用 `/dev/ttyACM<X>`、device 使用 `/dev/ttyGS<Y>`，並可用讀寫兩端測試。JetKVM 的做法符合這個標準路徑。

### 2. 既有實機 E2E 是很強的可行性證據

repo 的 remote-agent E2E 會在真實 host 上切換 `serial_console`、等待 `ttyACM` 出現、檢查 JetKVM 的 `/dev/ttyGS0`，並確認其他 keyboard/mouse functions 仍存在。另一項測試會從瀏覽器輸入字串，確認 host 的 `ttyACM` 收到，再由 host 寫回 serial port。這表示以下環節已被專案測試設計涵蓋：

1. USB composite 重新枚舉；
2. CDC-ACM host driver 綁定；
3. `/dev/ttyGS0` 可被 Go process 開啟；
4. 雙向 byte 搬運；
5. 與既有 KVM USB functions 共存。

本研究環境沒有 JetKVM 實機與遠端測試 host，因此沒有在本機重跑這組 hardware E2E；本報告引用的是固定 commit 內的官方原始碼與測試，而非宣稱已在本環境完成硬體驗證。

### 3. TCP 所需的網路與執行環境已存在

JetKVM 官方定位是 Linux 上的 Go 後端，並由裝置提供 web service：[本地 `README.md` L35-L51](../third_party/jetkvm/README.md#L35-L51)。正式程式已使用 Go `net` 套件處理網路，且 web server 可依組態 bind 到 IPv4/IPv6 的所有介面：[本地 `web.go` L631-L654](../third_party/jetkvm/web.go#L631-L654)。因此加入小型 TCP client/server 在軟體平台上沒有架構障礙。

## 必要改造

建議新增一個獨立的 `CDCACMTCPBridge` 元件，而不是把 raw TCP 硬塞進目前只服務單一 WebRTC session 的 `handleCDCACMChannel`。最小設計如下。

### 建議模式：JetKVM 主動連線到上位機

```text
JetKVM /dev/ttyGS0 -- net.DialTimeout --> 上位機固定 IP:port
```

優點是上位機只需開一個 TCP server，JetKVM 不必在管理網路新增對所有 host 開放的 listening port，也較容易穿越 NAT／防火牆。應提供：

- `enabled`、`remote_host`、`remote_port`、connect timeout、reconnect backoff；
- TLS 開關、上位機憑證驗證，最好再加 client certificate 或 device token；
- 連線狀態、byte counters、last error 及重連次數；
- `context` cancellation，讓 USB function 關閉、設定變更、程式退出時能乾淨停止；
- 兩個有界 copy loop（CDC→TCP、TCP→CDC），處理 partial write、EOF、timeout 與 backpressure；
- 明確限制同一時間只有一個 owner 開啟 `/dev/ttyGS0`。

### 替代模式：JetKVM 開 TCP server

若測試網路要求上位機主動連 JetKVM，可以 `net.Listen("tcp", bindAddr)`，但必須：

- 不要預設 bind `0.0.0.0` 且無認證；應可選指定管理介面／IP；
- 限制單一 client 或明確定義多 client 行為；
- 加 TLS 與 client authentication／allowlist；
- 對掃描、暴力連線、閒置連線及資源耗盡設限。

### 與現有 WebRTC console 的資源所有權

目前 `handleCDCACMChannel` 在 DataChannel open 時直接 `os.OpenFile("/dev/ttyGS0", O_RDWR)`，關閉 channel 才 close：[本地 `cdc_acm_console.go` L16-L24、L55-L60](../third_party/jetkvm/cdc_acm_console.go#L16-L24)。TCP bridge 若同時也開啟同一 tty，兩個 reader 會競爭資料，造成 byte 被不確定的一方取走。

建議至少採一種策略：

1. **互斥模式（推薦 PoC）**：TCP bridge 啟用時禁用 WebRTC USB Serial Console，UI 顯示 owner；
2. **中央 broker**：只有 broker 開 `/dev/ttyGS0`，WebRTC 與 TCP 都向 broker 訂閱／送資料，並明確定義 fan-out 和寫入仲裁；
3. **只保留 TCP**：若產品用途固定，移除／不建立 `cdcacm` DataChannel 路徑。

### 資料格式

CDC-ACM 與 TCP 在此用途都應視為 byte stream；應用層若有封包邊界，必須自行定義，例如：

- 固定 header + length + payload + CRC；
- newline-delimited records（只適合文字）；
- COBS/SLIP 等 framing；
- 或沿用測試電腦與上位機既有協定。

不能假設一次 USB read 等於一次 TCP read，也不能用 TCP packet 邊界當 message 邊界。

## 風險與對策

| 風險 | 證據／影響 | 對策 |
|---|---|---|
| USB composite endpoint／controller 資源 | CDC-ACM 不是唯一 USB function；目前預設還有 keyboard、兩種 mouse、mass storage、audio：[本地 `config.go` L175-L181](../third_party/jetkvm/config.go#L175-L181)。新增 function 後可能因硬體 endpoint/FIFO 配額在特定組合下失效。未合併的官方 repo [PR #1470](https://github.com/jetkvm/kvm/pull/1470) 也記錄 RV1106 上加入另一個 CDC function 時曾遇到 IN endpoint budget 問題；它不是本 commit 的保證，但足以列為實機驗證重點。 | PoC 分別測「預設 functions + CDC-ACM」與實際量產組合；長時間雙向壓測；必要時關閉不需要的 relative mouse、audio 或 mass storage。 |
| host 的 ModemManager 或其他程式搶先打開 serial port | 官方 E2E 明確等待 ModemManager probe 結束，避免它消耗測試資料：[本地 `ra-all.spec.ts` L2678-L2686](../third_party/jetkvm/ui/e2e/remote-agent/ra-all.spec.ts#L2678-L2686)。 | Linux host 加 udev 規則忽略該 VID/PID，或讓測試程式以明確 device identity 開啟；不要只依賴變動的 `/dev/ttyACM0` 編號。 |
| WebRTC 與 TCP 同時讀 `/dev/ttyGS0` | 兩個 process／goroutine 讀同一 tty 時資料分配不可控。 | 實作 exclusive owner 或中央 broker。 |
| USB 重新枚舉造成 device 消失 | 切換 USB classes 會 reconfigure gadget，host port 名稱可能改變，JetKVM fd 也會 EOF/error。 | 監聽設定生命週期；close/reopen `/dev/ttyGS0`；host 用 stable device identity；重連要有 backoff。 |
| TCP backpressure 與無界緩衝 | 上位機慢或網路中斷時，測試電腦仍可能持續寫資料。 | 有界 ring buffer；明定 drop/block policy；metrics 與告警；不要無限累積 RAM。 |
| raw TCP 無保密／認證 | 測試資料可能被竊聽、竄改；server port 可能成為進入測試環境的入口。 | 使用 TLS/mTLS、allowlist、最小 bind scope；停用時不 listen；不要把現有 web login 誤當成新 TCP port 的保護。 |
| TCP stream 無 message boundary | 合併或拆分 read 是正常行為，可能破壞上層「一包一 read」假設。 | 定義並測試 framing；所有 write 都處理 partial write。 |
| 電源與連線拓撲 | JetKVM 常由目標 USB 供電；目標斷電可能讓網關也掉電。官方提供 power/data splitter：[JetKVM Power Options](https://jetkvm.com/docs/peripheral-devices/alternative-power-sources)。 | ATE 場景用獨立 5V 電源，確保測試電腦重啟／斷電時網關仍在線。 |
| 韌體更新覆蓋客製 binary／啟動方式 | 只以 SSH 手動放 daemon 不足以保證 OTA 後存在。 | PoC 後把功能合併進 app 或使用系統支援的 persistent userdata init；固定 app/system 版本並驗證 OTA。 |
| 授權 | 本 repo 是 GPL-2.0：[本地 `LICENSE`](../third_party/jetkvm/LICENSE)、[GitHub repo](https://github.com/jetkvm/kvm)。修改並散布 binary 時需做授權審查。 | 在產品化前由法務／開源合規確認 source offer、notice 與衍生作品義務。 |

## 建議驗證 PoC

### Phase 0：不改碼，確認現有 USB 路徑

1. JetKVM 以獨立電源供電；USB-C data 接測試電腦，RJ45 接管理網路。
2. JetKVM Web UI → USB Devices → Custom，啟用 USB Serial Console（現有 UI 位置可由 [本地 `UsbDeviceSetting.tsx` L247-L265](../third_party/jetkvm/ui/src/components/UsbDeviceSetting.tsx#L247-L265) 確認）。
3. Linux 測試電腦確認 `/dev/ttyACM*`；Windows 確認新增 COM port。JetKVM SSH 確認 `/dev/ttyGS0`。
4. 用現有 USB Serial Console 做雙向測試，先證明 USB cable、host driver、gadget function 與 tty 都正常。

**通過條件**：雙向文字與至少 1 MiB binary payload 的 SHA-256 完全一致；重插 USB 後能恢復；keyboard/mouse 仍工作。

### Phase 1：獨立 bridge binary

先做不改 JetKVM 主程式的最小 Go daemon：

- 開 `/dev/ttyGS0`；
- 主動 TLS/TCP 連上位機；
- 兩方向 `io.CopyBuffer`；
- 指數 backoff 重連；
- SIGTERM 可乾淨退出；
- 單一 owner，不同時開 web CDC console。

這一步能快速回答「真實 ATE 流量、網路、防火牆及上位機程式是否合用」，也把 USB 風險與產品整合風險分開。

### Phase 2：可靠性／壓力測試

至少測：

1. 目標→上位機、上位機→目標及同時雙向，各 1 GiB；
2. 24 小時持續流量，記錄 byte mismatch、reconnect、CPU、RAM；
3. 拔插 USB、拔插 RJ45、上位機重啟、JetKVM 重啟、測試電腦重啟；
4. 上位機刻意停止 read，驗證 backpressure 與 buffer policy；
5. keyboard/mouse/video/audio/mass-storage 按實際使用組合併發；
6. Linux ModemManager、Windows COM port 重新枚舉與 driver 行為；
7. port scan、錯誤憑證、未授權 client、長時間 idle connection；
8. OTA／app 更新後自動啟動與設定保存。

### Phase 3：整合到 JetKVM app

PoC 通過後，再把 bridge 做成正式 deep module：

- 自有 config、lifecycle、metrics 與 logger；
- JSON-RPC／UI 只控制設定與觀測，不直接擁有 tty；
- 與 USB reconfiguration、app shutdown 及 existing CDC WebRTC console 協調；
- unit test 用 pseudo-terminal／`net.Pipe`，hardware E2E 延伸現有 `ra-all.spec.ts`。

## 最終判定

| 問題 | 回答 |
|---|---|
| 目前 JetKVM 能否讓測試電腦把資料送入 USB CDC-ACM？ | **可以，已合併且預設關閉。** |
| 目前能否把該資料送到上位機？ | **可以，現況目的端是 JetKVM Web UI，傳輸是 WebRTC DataChannel。** |
| 目前是否已提供「USB CDC → raw TCP/IP socket」？ | **沒有。** |
| 是否需改硬體？ | **依現有證據，通常不需要；CDC-ACM、USB device mode、RJ45 與 Linux/Go 執行環境都已存在。** |
| 改軟體的難度？ | **核心 bridge 不高，但產品化的斷線、backpressure、互斥、安全與 OTA 整合屬中等工程量。** |
| 是否值得做 PoC？ | **值得，且建議優先。現有程式已把技術風險最大的 USB gadget 與 tty 路徑打通。** |

建議決策：**採「JetKVM 主動以 TLS/TCP 連上位機」的單 client PoC，先禁用同時開啟 WebRTC USB Serial Console，使用實際 ATE 資料跑 24 小時與故障注入；通過後再整合進主程式。**

## Primary sources

1. JetKVM 官方原始碼，固定 commit [`b3c29a44d9e2862b8ff7530830781803ce27b060`](https://github.com/jetkvm/kvm/tree/b3c29a44d9e2862b8ff7530830781803ce27b060)。
2. JetKVM 官方合併 PR：[feat: add USB CDC-ACM serial console gadget #1352](https://github.com/jetkvm/kvm/pull/1352)。
3. JetKVM 官方文件：[Quick Start](https://jetkvm.com/docs/getting-started/quick-start)、[Developer Tools](https://jetkvm.com/docs/advanced-usage/developing)、[Power Options](https://jetkvm.com/docs/peripheral-devices/alternative-power-sources)。
4. Linux Kernel 官方文件：[Linux USB gadget configured through configfs](https://docs.kernel.org/usb/gadget_configfs.html)、[Gadget Testing — ACM function](https://docs.kernel.org/usb/gadget-testing.html#testing-the-acm-function)、[Linux Gadget Serial Driver](https://docs.kernel.org/usb/gadget_serial.html)。
5. IETF：[RFC 8831 — WebRTC Data Channels](https://www.rfc-editor.org/rfc/rfc8831.html)、[RFC 9293 — TCP](https://www.rfc-editor.org/rfc/rfc9293.html)。
6. JetKVM 官方 repo 未合併設計參考（只用於風險辨識，不視為現況功能）：[CDC-NCM PR #1470](https://github.com/jetkvm/kvm/pull/1470)。
