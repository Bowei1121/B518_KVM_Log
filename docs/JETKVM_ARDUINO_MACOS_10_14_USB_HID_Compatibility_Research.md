# JetKVM 與 Arduino UNO R4 在 macOS 10.14 的 USB HID 相容性研究

- 研究日期：2026-08-28
- JetKVM 基準：`b3c29a44d9e2862b8ff7530830781803ce27b060`
- Arduino 應用韌體：`B518_ARDUINO_MVP 1.0.4`
- 本機 Arduino 平台：`arduino:renesas_uno 1.6.0`、Keyboard `1.0.6`、Mouse `1.0.1`

## 結論

有機會，而且 UNO R4 的硬體本身已具備 USB device／HID 能力；目前不應下結論說「Arduino 無法被 Mojave 辨識為 HID」。專案既有現場紀錄明確寫的是「可被 macOS 列舉為 HID」，待查的是 HID interface 是否成功被 IOHID 驅動綁定，以及 interrupt IN endpoint/report completion 是否正常。因此要把問題拆成三層：

1. **USB device enumeration**：Mac 是否看到 VID/PID、configuration 及各 interface。
2. **HID driver binding**：class `0x03` 的 interface 下方是否出現 Apple 的 HID service／IOHIDDevice。
3. **HID report delivery**：驅動已綁定後，interrupt IN report 是否真的完成並變成鍵鼠事件。

目前證據最支持第 3 層的 firmware/core 傳送狀態問題，其次才是第 2 層的 descriptor 形狀相容性。JetKVM 的相容優勢不是 VID/PID，而是把 keyboard、absolute mouse、relative mouse 拆成三個獨立 HID interface，其中 keyboard 與 relative mouse 又宣告 Boot subclass/protocol；Arduino 1.0.4 則把三個 top-level collections、report ID 1/2/3 合併在一個 non-Boot HID interface。後者符合一般 HID report-protocol 架構，但對舊主機的解析、綁定和故障隔離都較不保守。

最值得先做的修正是：**不得在 endpoint ready 前呼叫目前 core 的 `HID().SendReport()`；所有 HID report 必須有 ready 檢查、有限等待、失敗回傳，且不能讓 CDC/TCP 主迴圈永久卡住。** 第二階段再 A/B 測試拿掉 absolute collection；若仍不能通過，才修改 core，把鍵盤與相對滑鼠拆成獨立 Boot HID interfaces，逼近 JetKVM 的 descriptor topology。

> 限制：本研究沒有故障的 Mojave 主機、線上 JetKVM 或 USB bus capture。因此以下會明確區分「原始碼證實」與「仍須現場確認」，不宣稱任何單一候選已經是唯一根因。

## 已由原始碼證實的事實

### 實際 Arduino 目標與版本

- 1.0.4 主韌體明列目標為 **UNO R4 Minima 或 UNO R4 WiFi + W5100**，同時 include `Keyboard.h`、`Mouse.h`、`HID.h`：[主韌體第 1–15 行](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/B518_Arduino_MVP_Test/B518_Arduino_MVP_Test.ino>)。
- Repo 無法再唯一判定現場實體是 Minima 還是 WiFi；這不是研究中可安全猜測的資訊。須在故障站送 `GET_INFO` 讀 `BOARD`，或以 USB VID/PID（Minima `0069`、WiFi native `006D`）確認後，才能選用正確的 exact configuration bytes。
- 唯一應用韌體版本來源是 `firmware_version.h`，值為 `1.0.4`，並用 `ARDUINO_UNOR4_MINIMA`／`ARDUINO_UNOR4_WIFI` 回報實際編譯目標：[版本檔](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/B518_Arduino_MVP_Test/firmware_version.h>)。
- **應用韌體 1.0.4 不等於 Arduino board core 1.0.4。** 本機現在安裝的是 Renesas core 1.6.0；若現場板當初由另一台電腦編譯，必須從其 verbose build log／已保存 artifact 確認實際 core 與 library 版本，不能由 `FW=1.0.4` 推定。
- 專案現場摘要已記錄 Arduino「可被 macOS 列舉為 HID」，待驗證重點是 report 傳送與完成回覆：[PROJECT_SUMMARY.md](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/PROJECT_SUMMARY.md>)。

### Arduino 1.0.4 的 descriptor 與 report 路徑

- 本機 Renesas core 1.6.0 的 device descriptor 最終把 `bDeviceClass/bDeviceSubClass/bDeviceProtocol` 都設為 `0`，表示 class 由 interface 決定；所以「它把整台複合裝置錯宣告為 CDC」**不是此版本的根因**。VID/PID 是 Minima `2341:0069`、WiFi native USB `2341:006D`。官方同版來源見 [USB.cpp 第 75–97 行](https://github.com/arduino/ArduinoCore-renesas/blob/1.6.0/cores/arduino/usb/USB.cpp#L75-L97)。
- core 動態組出 CDC（2 interfaces）加 HID（1 interface）；HID 固定為 class `03`、subclass `00`、protocol `00`、interrupt IN endpoint `0x83`、max packet `64`、interval `10 ms`。相關來源是本機 `~/Library/Arduino15/packages/arduino/hardware/renesas_uno/1.6.0/cores/arduino/usb/USB.cpp:119-219`，官方同版為 [USB.cpp](https://github.com/arduino/ArduinoCore-renesas/blob/1.6.0/cores/arduino/usb/USB.cpp#L119-L219)。
- Mouse library append 54-byte collection（report ID 1，4 data bytes）；Keyboard append 47-byte collection（report ID 2，8 data bytes）；本專案再 append 58-byte absolute mouse collection（report ID 3，5 data bytes）。三者合成同一個 **159-byte report descriptor**。本專案 absolute bytes 與 `SendReport(3, ..., 5)` 見[主韌體第 40–105 行](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/B518_Arduino_MVP_Test/B518_Arduino_MVP_Test.ino>)；Keyboard 官方來源見 [Keyboard 1.0.6](https://github.com/arduino-libraries/Keyboard/blob/1.0.6/src/Keyboard.cpp#L31-L83)。
- 1.0.4 `setup()` 在 USB 初始化後立即依序呼叫 `Keyboard.releaseAll()`、兩次 `Mouse.release()`：[主韌體第 150–166 行](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/B518_Arduino_MVP_Test/B518_Arduino_MVP_Test.ino>)。其中 `Keyboard.releaseAll()` 一定送一包 report。
- Renesas HID core 的 `SendReport()` 先永久 busy-wait `_done`，再把 `_done=false` 並呼叫 `tud_hid_report()`；它沒有先查 `tud_hid_ready()`、沒有 timeout，也沒有在 submit 失敗時把 `_done` 還原。只有 transfer-complete callback 會令 `_done=true`。來源：本機 `.../renesas_uno/1.6.0/libraries/HID/HID.cpp:21-30`，官方同版 [HID.cpp](https://github.com/arduino/ArduinoCore-renesas/blob/1.6.0/libraries/HID/HID.cpp#L21-L30)。TinyUSB 的 `tud_hid_n_report()` 在 endpoint claim 或 transfer 失敗時可直接回 `false`，而 completion callback 只代表成功送完；見官方 [TinyUSB hid_device.c](https://github.com/hathach/tinyusb/blob/master/src/class/hid/hid_device.c)。

因此可由程式邏輯推出一個具體失敗序列：若開機時的 `Keyboard.releaseAll()` 在 host 尚未 configure／arm endpoint 時 submit 失敗，`_done` 仍會留在 `false`；下一次真正的鍵鼠命令便可永久卡在 busy-wait。這個缺陷**已由 source 證實存在**；但「Mojave 現場確實走到這條路徑」仍須用下文的 ACK/OK、ready flag 或 USB trace 證實。

### JetKVM 的 descriptor 與 report 路徑

- JetKVM 用 Linux configfs 建 gadget，USB 2.0、VID/PID `1d6b:0104`、產品 `JetKVM USB Emulation Device`；configuration 設 `bmAttributes=0xa0`、configfs `MaxPower=250` mA（轉成 USB 2.0 descriptor 時才以 2 mA 為一單位）：[config.go 第 27–68 行](../third_party/jetkvm/internal/usbgadget/config.go)、[Linux `encode_bMaxPower`](https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/composite.c)。
- 預設啟用 keyboard、absolute mouse、relative mouse 與 mass storage；serial console 並未在預設 `Devices` literal 中設 true：[usbgadget.go 第 17–46 行](../third_party/jetkvm/internal/usbgadget/usbgadget.go)。實際出貨設定是否另行啟用功能，須在設備讀 configfs 或抓 descriptor。
- 三種 HID 是三個 configfs functions，依序為 `hid.usb0`、`hid.usb1`、`hid.usb2`，而不是三個 report ID 塞進同一 interface：keyboard 設 subclass `1`/protocol `1`/8 bytes；absolute mouse 設 subclass `0`/protocol `2`/6 bytes；relative mouse 設 subclass `1`/protocol `2`/5 bytes。來源：[keyboard](../third_party/jetkvm/internal/usbgadget/hid_keyboard.go)、[absolute mouse](../third_party/jetkvm/internal/usbgadget/hid_mouse_absolute.go)、[relative mouse](../third_party/jetkvm/internal/usbgadget/hid_mouse_relative.go)。
- keyboard 與 relative mouse 的 Boot subclass/protocol，讓支援 Boot protocol 的 host 可使用固定格式；HID 1.11 定義 Boot Interface Subclass、Keyboard/Mouse protocol 與 Boot/Report protocol：[USB-IF HID 1.11](https://www.usb.org/document-library/device-class-definition-hid-111)。Apple 現行 DriverKit 的 [`setProtocol`](https://developer.apple.com/documentation/hiddriverkit/iouserusbhosthiddevice/setprotocol) 只能作通用機制補充，**不是 Mojave 10.14 API 或行為證據**。
- JetKVM 的 bytes 直接寫入 configfs `report_desc`，functions 再以 symlink 加到 configuration，最後 bind UDC：[config_tx.go 第 143–167、224–274 行](../third_party/jetkvm/internal/usbgadget/config_tx.go)。Linux 官方文件確認 configfs HID function 的 `protocol`、`subclass`、`report_length`、`report_desc` 含義：[Linux gadget testing](https://github.com/torvalds/linux/blob/master/Documentation/usb/gadget-testing.rst#6-hid-function)。

## Descriptor 逐欄位比較

### Device／configuration／interface

| 欄位 | Arduino 1.0.4 + 本機 core 1.6.0 | JetKVM `b3c29a44` | 相容性意義 |
|---|---|---|---|
| USB version | `bcdUSB=0200` | `bcdUSB=0200` | 相同 |
| VID:PID | Minima `2341:0069`；WiFi native `2341:006D` | `1d6b:0104`，可自訂 | Apple 依 class/interface 也能匹配；JetKVM 的 VID 並非「Apple HID 白名單」證據 |
| device class/subclass/protocol | `00/00/00` | 原始碼未寫這三欄；configfs 預設需由實機 descriptor 確認，預期是 per-interface | Arduino 1.6.0 此處已是 composite-friendly；不能據此判定差異 |
| configuration attributes | `C0`（self-powered），500 mA | `A0`（remote wake，未設 self-powered），250 mA | Arduino 的 self-powered 宣告是否符合實際供電要查硬體；不是目前最像 HID-only 問題的根因 |
| interface topology | WiFi：CDC control + CDC data + **單一 HID**；Minima 另有 DFU runtime interface | **三個獨立 HID**，另可有 MSC/ACM | JetKVM 的每個 HID 可獨立匹配、解析、失敗隔離 |
| HID interface class | `03` | 各 HID 都是 `03`（Linux function driver 生成） | 都能進入 class matching |
| subclass/protocol | `00/00`（none/report-only） | keyboard `01/01`；relative mouse `01/02`；absolute `00/02` | Jet 的 keyboard/relative mouse 提供 Boot 路徑；Arduino 完全依賴 159-byte report descriptor |
| HID spec version | `0111` | Linux `f_hid` 版本依 JetKVM image kernel；上游目前為 `0101`，須實機確認 | 版本不同本身不等於不相容 |
| interrupt endpoint | IN `83`、64-byte、10 ms | 每 function 獨立 IN；keyboard 另有 OUT（`no_out_endpoint=0`），動態 endpoint number/interval 須實機抓取 | Arduino 所有 report 共用一條 IN；JetKVM 可獨立排程 |

Arduino 在本機 core 1.6.0 下可由 TinyUSB macro 精確展開：UNO R4 WiFi 的 configuration header 是 `09 02 64 00 03 01 00 C0 FA`，HID 區段為：

```text
09 04 02 00 01 03 00 00 00   # interface 2, 1 EP, HID, non-Boot
09 21 11 01 00 01 22 9F 00   # HID 1.11, report descriptor 0x009F = 159
07 05 83 03 40 00 0A         # interrupt IN 0x83, 64 bytes, 10 ms
```

UNO R4 Minima 因 `CFG_TUD_DFU_RUNTIME=1`，configuration header 改為 `09 02 76 00 04 01 00 C0 FA`，HID interface number 也由 `02` 變成 `03`；其餘 HID/HID endpoint bytes 相同。現場必須先以 `BOARD` 或 VID/PID 確認板型，不能混用兩份 exact bytes。

JetKVM 的 interface number、endpoint address、HID descriptor version及 interval 是 Linux gadget driver／UDC 動態產物，repo 沒有固定完整 configuration bytes；把推定值偽裝成 exact bytes 反而會誤導。應從實機 `lsusb -v` 或 Mojave USB capture 取得後，才完成 device/config/interface 的真正逐 byte 比對。Linux 上游 `f_hid.c` 證實 interface class 固定 HID、subclass/protocol 與 endpoint 欄位動態生成：[Linux f_hid.c](https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/function/f_hid.c)。

### HID report descriptor exact bytes

Arduino 單一 interface 的 report descriptor 是以下三段串接，總長 159 bytes：

```text
# ID 1 relative mouse, 54 bytes, input packet = 01 + buttons/X/Y/wheel
05 01 09 02 A1 01 09 01 A1 00 85 01 05 09 19 01 29 03 15 00
25 01 95 03 75 01 81 02 95 01 75 05 81 03 05 01 09 30 09 31
09 38 15 81 25 7F 75 08 95 03 81 06 C0 C0

# ID 2 keyboard, 47 bytes, input packet = 02 + modifier/reserved/6 keys
05 01 09 06 A1 01 85 02 05 07 19 E0 29 E7 15 00 25 01 75 01
95 08 81 02 95 01 75 08 81 03 95 06 75 08 15 00 25 73 05 07
19 00 29 73 81 00 C0

# ID 3 absolute mouse, 58 bytes, input packet = 03 + buttons/Xlo/Xhi/Ylo/Yhi
05 01 09 02 A1 01 09 01 A1 00 85 03 05 09 19 01 29 03 15 00
25 01 95 03 75 01 81 02 95 01 75 05 81 03 05 01 09 30 09 31
15 00 26 FF 7F 35 00 46 FF 7F 75 10 95 02 81 02 C0 C0
```

JetKVM 則是三個獨立 descriptors：

```text
# keyboard, 63 bytes, no report ID, 8-byte input + 1-byte LED output
05 01 09 06 A1 01 05 07 19 E0 29 E7 15 00 25 01 75 01 95 08
81 02 95 01 75 08 81 03 95 05 75 01 05 08 19 01 29 05 91 02
95 01 75 03 91 03 95 06 75 08 15 00 25 FF 05 07 19 00 29 FF
81 00 C0

# relative mouse, 61 bytes, no report ID, buttons/X/Y/wheel/AC Pan = 5 bytes
05 01 09 02 A1 01 09 01 A1 00 05 09 19 01 29 08 15 00 25 01
95 08 75 01 81 02 05 01 09 30 09 31 09 38 15 81 25 7F 75 08
95 03 81 06 05 0C 0A 38 02 15 81 25 7F 75 08 95 01 81 06 C0 C0

# absolute mouse, 93 bytes; ID 1 = 6 bytes，ID 2 = wheel/pan 3 bytes
05 01 09 02 A1 01 85 01 09 01 A1 00 05 09 19 01 29 05 15 00
25 01 75 01 95 05 81 02 95 01 75 03 81 03 05 01 09 30 09 31
16 00 00 26 FF 7F 36 00 00 46 FF 7F 75 10 95 02 81 02 C0 85
02 09 38 15 81 25 7F 35 00 45 00 75 08 95 01 81 06 05 0C
0A 38 02 15 81 25 7F 75 08 95 01 81 06 C0
```

USB-IF HID 規格的核心機制是 host 先讀 HID/report descriptors，再依 report item 解析 input；descriptor 使用 Generic Desktop Mouse `Usage=2` 和 Keyboard `Usage=6`：[HID 1.11](https://www.usb.org/document-library/device-class-definition-hid-111)、[HID Usage Tables](https://usb.org/document-library/hid-usage-tables-15)。

## macOS 10.14 相容性機制

Apple 的公開架構資料足以支持下列流程，但沒有公開文件保證「Mojave 對某個 159-byte descriptor 有已知 bug」：

1. USB device 出現後形成 device nub。
2. composite driver 選 configuration，為每個 interface 建 interface nub。
3. HID class driver按 `bInterfaceClass/SubClass/Protocol` 對 interface matching，取得 HID report descriptor並建立 HID elements／event service。
4. driver 對 interrupt IN pipe 排入讀取；裝置送 report，完成傳輸後才會得到 completion。

Apple 官方特別提醒 composite device 的「device」與「interfaces」是不同 registry objects，HID 鍵盤驅動是 interface driver：[USB Device Overview](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/USBBook/USBOverview/USBOverview.html)、[IOKit Families](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/Families_Ref/Families_Ref.html)。這正是不能用「System Information 看得到 Arduino」直接等同「IOHID 已綁定且 report 正常」的原因。

JetKVM 的三個獨立 interfaces，會分別形成 matching/pipe 路徑。Arduino 則只有一個 HID interface；其中任一 collection 讓舊 parser 拒絕 descriptor、或唯一 interrupt pipe 沒被 arm，鍵盤、相對滑鼠、絕對滑鼠會一起失效。這是**結構上的風險推論**，不是已證實的 Mojave 缺陷。

## 候選根因排序

| 排名 | 候選 | 目前證據 | 如何一槍鑑別 |
|---:|---|---|---|
| 1 | Arduino core 的 report submit/completion 狀態被開機早送或一次失敗毒死，之後永久 busy-wait | source 已證實缺少 ready/timeout/失敗復原；1.0.4 又在 `setup()` 主動 `releaseAll()`；現場描述符合「列舉成功但 report 不動」 | 看 CDC 是否先回 `ACK` 而永遠無 `OK`；加 `tud_mounted/tud_hid_ready` 與 submit return telemetry；USB trace 看 endpoint 是否有成功 IN transaction |
| 2 | Mojave 未綁定／未 arm Arduino 的單一 non-Boot、多 collection HID interface | JetKVM 拆成獨立 Boot interfaces；Arduino 為單一 `00/00` interface + 159-byte descriptor | `ioreg` 確認 HID interface 下有無 Apple HID service；A/B 移除 ID 3 absolute collection；再做單一 mouse-only sketch |
| 3 | 現場實際 core/library/board target 與本研究不同 | repo 只鎖應用 FW 1.0.4，沒有鎖 build toolchain；Minima/WiFi VID/PID 不同 | 保存 Arduino verbose compile output、FQBN、platform/library versions與燒錄 binary hash；抓實際 descriptors後比對 |
| 4 | report length／節奏／共享 endpoint 問題 | main 1.0.4 的 ID 與長度在 source 中一致；但多 report 共用 endpoint且無 backpressure timeout | 抓每包長度；依序測 mouse-only、keyboard-only、兩者、再加 absolute；每包等 `tud_hid_ready()` |
| 5 | USB topology、供電宣告、hub/cable、舊 Mac USB controller 狀態 | Arduino configuration 宣告 self-powered/500 mA；JetKVM 宣告 bus-powered + remote-wake/250 mA；尚無現場拓撲資料 | 同一實體 port/cable/hub 交叉換 Jet/Arduino；直連；冷開機；記錄 port/location ID與 bus errors |
| 6 | 應用層誤把沒有事件視為沒有 HID | 已知有「列舉為 HID」紀錄；Secure Input、焦點、鍵盤配置或游標邊界可影響觀察 | 用 `ioreg`/IOHID existence 與原始 report capture，不以螢幕是否移動作唯一判準 |

不應優先懷疑 VID/PID：Apple 官方說 USB/HID driver matching 可對 interface class/subclass/protocol進行，JetKVM 使用 Linux Foundation VID 也不是 Apple 專用 ID：[Apple interface matching](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/USBBook/USBOverview/USBOverview.html)。

## Arduino 可行方案

### A. 最小變更，先保住 1.0.4 架構（建議第一步）

1. 移除 `setup()` 中未確認 ready 就送出的 `Keyboard.releaseAll()`；改為 `tud_mounted() && tud_hid_ready()` 後只做一次，或乾脆等第一個合法命令。
2. 不直接走目前無限 busy-wait 的 `HID().SendReport()`。建立統一 `sendHidReport()`：有限等待 ready、檢查 submit boolean、超時回 `ERR:HID_NOT_READY`／`ERR:HID_SEND_FAILED`，且主 loop/CDC/TCP 永遠能繼續。
3. report 之間必須等上一包完成／ready；不可只靠固定 delay。斷線或 USB reset 後清除傳送狀態。
4. 先建三個可燒錄 A/B artifacts：relative mouse only、mouse+keyboard、mouse+keyboard+absolute。若前兩個過、第三個不過，才有證據指向 absolute collection/parser。

本 repo 的非 main 分支 `BT_Claude` 1.1.0（commit `16a38f0`）已示範 ready guard、延後 startup release、可編譯停用 absolute；非 main 分支 `BT-Codex` 1.0.5（commit `8107607`）已示範 TinyUSB direct send + 180 ms timeout。它們是診斷原型，不是已證實修復。尤其 `BT-Codex` 的 `sendMouseReport()` 送 5 data bytes（多一個 pan），但現有 Arduino ID 1 descriptor 只宣告 4 data bytes，正式採用前必須改成 descriptor/report 完全一致。

### B. 修改 Renesas core，仿照 JetKVM 的 interface topology

若最小變更仍不能讓 Mojave 穩定綁定，修改／fork core：

- 將 `CFG_TUD_HID` 從 1 擴充為 2 或 3 instances，分配不同 interrupt endpoints。
- keyboard interface 設 Boot subclass/protocol `1/1`，8-byte、無 report ID；relative mouse 設 `1/2`，使用保守 Boot-compatible report；absolute mouse 保持獨立 non-Boot interface。
- 分別提供 report descriptor callback、ready state、completion state，不共用一個 `_done`。
- 保持 device class `00/00/00`，CDC 用 IAD，確保 `wTotalLength`、`bNumInterfaces`、endpoint addresses、packet sizes一致。
- 先確認 RA4M1 USB peripheral/TinyUSB port 可分配足夠 interrupt endpoints，再做 descriptor compliance 與多 OS 回歸。

這是最接近 JetKVM 的純 Arduino 解法，但會讓專案必須維護自有 board core；Arduino IDE 更新、燒錄救援、CDC 與 HID 回歸都要納入 release 流程。

### C. 換板或外接專用 HID MCU

- 選用在 Mojave 已實機認證、可控制 descriptors 的原生 USB 板（例如成熟的 ATmega32U4/SAMD/TinyUSB 平台），W5100 邏輯再移植。
- 或保留 UNO R4 做 TCP/W5100 與業務 protocol，另接一顆專用 HID MCU；兩者用 UART/SPI 傳命令。這能把 Ethernet/CDC 風險與舊 Mac HID 相容性隔離，也是量產上最容易鎖定 descriptor/binary 的方案。
- 若板級變更可接受，直接用 JetKVM/Linux gadget 類平台做 HID+network gateway，自由度最高，但成本、開機時間、映像維護與資安面較 Arduino 大。

不論哪個方案，「能被 System Information 看見」都不是驗收完成；必須同時驗證 enumeration、driver binding、report delivery、斷線恢復與 CDC/TCP 不被 HID 卡死。

## 現場鑑別步驟

### 0. 固定變因

- 同一台 Mojave 10.14.x、同一實體 USB port、同一 cable；先直連，不經 hub。
- 記錄 Mac build、機型、USB controller、Arduino 絲印（Minima/WiFi）、VID/PID、FW 回覆、binary SHA-256。
- 每個 artifact 保存 Arduino IDE verbose compile log，至少含 FQBN、Renesas core、Keyboard、Mouse 版本。

### 1. 證明 device enumeration

依序插 JetKVM 與 Arduino，各保存：

```bash
system_profiler SPUSBDataType > usb-system-profiler.txt
ioreg -p IOUSB -l -w 0 > usb-ioreg.txt
```

確認 VID/PID、configuration、interface count、class/subclass/protocol、endpoint。Apple 說 `ioreg` 是 I/O Registry Explorer 的 CLI 對應工具：[The I/O Registry](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/TheRegistry/TheRegistry.html)。

### 2. 證明 HID binding，而非只看到 USB device

```bash
ioreg -r -c IOHIDDevice -l -w 0 > hid-ioreg.txt
ioreg -r -c IOUSBInterface -l -w 0 > usb-interface-ioreg.txt
```

在 USB service plane 找 Arduino 的 HID interface，再看其 child/provider-client chain 是否有 Apple HID service，以及 IOHIDDevice 是否出現 Mouse usage `1/2`、Keyboard usage `1/6`。若 USB HID interface 存在但沒有 IOHID child，才可稱為「interface 未被 IOHID 綁定／report descriptor parse 失敗候選」。不要只用鍵盤設定助理或游標是否移動判斷。

### 3. 證明 endpoint/report delivery

- 用 USB Prober／相容的硬體 USB analyzer 取得完整 device、configuration、HID report descriptors及 control/interrupt transfers。
- 送一個 `K_KEY:TAB` 與一個短 `M_DELTA`，同步保存 CDC：是否看到 `ACK`、`OK`、或 ACK 後停止回應。
- 診斷 firmware 回報 `tud_mounted()`、`tud_hid_ready()`、submit result、completion count、timeout count；嚴禁 telemetry 本身無限阻塞。
- 若 `tud_hid_ready=false` 且 IOHID 未綁定，回到 descriptor A/B；若 ready 曾為 true、submit 成功但 completion 不回，查 host pipe/reset；若 ACK 後整個 CDC 死亡，優先命中 core busy-wait。

### 4. 最小化 A/B

依序燒錄並每次完全拔插：

1. CDC only（基線）。
2. CDC + relative mouse only，單一簡短 descriptor。
3. CDC + keyboard only。
4. CDC + relative mouse + keyboard（IDs 1/2）。
5. 再加 absolute mouse（ID 3）。
6. core multi-interface：Boot keyboard + Boot relative mouse。

若第 2/3 過、第 4 不過，指向單 interface multi-report/排程；若第 4 過、第 5 不過，指向 absolute collection；若 2 就不過但 multi-interface Boot 版本過，指向 interface subclass/protocol/topology；若全部 binding 正常但 report 不動，指向送包/completion。

## 驗收矩陣

| 測項 | Mojave 10.14 | Catalina 10.15 | 較新 macOS | Windows 10/11 | Linux | 通過標準 |
|---|---:|---:|---:|---:|---:|---|
| 冷插 enumeration 100 次 | 必測 | 必測 | 必測 | 必測 | 必測 | 100/100 有正確 interfaces，無反覆重枚舉 |
| IOHID binding | 必測 | 必測 | 必測 | 對應 HID driver | `hid-generic` | keyboard/mouse usages 全出現 |
| 第一包（開機後立即） | 必測 | 必測 | 必測 | 必測 | 必測 | 不丟失、不凍結 CDC |
| 鍵盤 press/release 1,000 次 | 必測 | 必測 | 必測 | 必測 | 必測 | 無 stuck key、順序正確 |
| 相對滑鼠 10,000 reports | 必測 | 必測 | 必測 | 必測 | 必測 | 無 freeze；送/完成計數一致 |
| absolute mouse | 若需求保留則必測 | 同左 | 同左 | 同左 | 同左 | 座標與 button report 長度完全符合 descriptor |
| CDC + HID 同時滿載 8 小時 | 必測 | 必測 | 必測 | 必測 | 必測 | CDC/TCP 無 starvation；HID timeout 可恢復 |
| 拔插／sleep-wake 100 次 | 必測 | 必測 | 必測 | 必測 | 必測 | endpoint state reset，無需重燒／重開 Mac |
| hub／無 hub、各部署 cable | 必測 | 必測 | 抽測 | 抽測 | 抽測 | 結果一致；否則鎖定認證拓撲 |
| Boot/Recovery（若為需求） | 必測 | 適用時 | 適用時 | BIOS/UEFI | boot console | 鍵鼠在 OS driver 前可用 |

Release gate 應另外要求：descriptor dump、toolchain manifest、firmware binary hash、Mojave evidence bundle（USB tree、HID tree、CDC log、transfer capture）全數隨版本保存。

## 建議決策順序

1. 先在故障 Mojave 收集三層證據；這一步可能立即證明目前其實已綁定 HID，只是 firmware 卡死。
2. 用「ready guard + timeout + 不在 setup 送包」的最小 firmware 做 A/B；它的風險與成本最低。
3. 再以停用 absolute collection 的 artifact 隔離 parser 問題。
4. 只有在「單純 mouse/keyboard report descriptor仍無法綁定」時，才投入 core multi-interface Boot HID。
5. 若交期或量產風險不允許維護 forked core，選已在 Mojave 認證的專用 HID MCU；UNO R4 保留 network/bridge 職責。

## 第一手來源索引

- 本專案：[Arduino 1.0.4 主韌體](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/B518_Arduino_MVP_Test/B518_Arduino_MVP_Test.ino>)、[版本檔](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/B518_Arduino_MVP_Test/firmware_version.h>)、[現場摘要](<https://github.com/Bowei1121/B518_205_207_ATE/blob/44fcd9140c32250a767c2b2b76aadf14bf718c80/B518%20ATE%20MVP%20Demo/PROJECT_SUMMARY.md>)。
- JetKVM 固定 commit 的本地原始碼：[USB gadget config](../third_party/jetkvm/internal/usbgadget/config.go)、[transaction](../third_party/jetkvm/internal/usbgadget/config_tx.go)、[keyboard](../third_party/jetkvm/internal/usbgadget/hid_keyboard.go)、[relative mouse](../third_party/jetkvm/internal/usbgadget/hid_mouse_relative.go)、[absolute mouse](../third_party/jetkvm/internal/usbgadget/hid_mouse_absolute.go)。
- Arduino 官方：[Renesas core 1.6.0 USB.cpp](https://github.com/arduino/ArduinoCore-renesas/blob/1.6.0/cores/arduino/usb/USB.cpp)、[HID.cpp](https://github.com/arduino/ArduinoCore-renesas/blob/1.6.0/libraries/HID/HID.cpp)、[Keyboard 1.0.6](https://github.com/arduino-libraries/Keyboard/blob/1.0.6/src/Keyboard.cpp)、[Mouse 1.0.1](https://github.com/arduino-libraries/Mouse/blob/1.0.1/src/Mouse.cpp)。
- 規格／kernel：[USB-IF HID 1.11](https://www.usb.org/document-library/device-class-definition-hid-111)、[HID Usage Tables](https://usb.org/document-library/hid-usage-tables-15)、[Linux HID gadget 文件](https://github.com/torvalds/linux/blob/master/Documentation/usb/gadget_hid.rst)、[Linux `f_hid.c`](https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/function/f_hid.c)。
- Apple 官方：[USB Device Overview](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/USBBook/USBOverview/USBOverview.html)、[IOKit Families](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/Families_Ref/Families_Ref.html)、[I/O Registry](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/TheRegistry/TheRegistry.html)。現行 [DriverKit HID protocol API](https://developer.apple.com/documentation/hiddriverkit/iouserusbhosthiddevice/setprotocol) 僅作通用概念補充，不用作 Mojave 實作證明。
