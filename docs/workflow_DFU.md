# DFU 設備工作流程

更新日期：2026-09-03（Asia/Shanghai）

本文件是 DFU 工作流程的單一來源。JetKVM 專案只以 HDMI 串流畫面判讀並經 USB HID 操作；本機 Log 的讀取仍由獨立專案負責。本 repo 只會送出 `Command+Shift+M` 快捷鍵要求該程式開始監控，不讀取或比對任何本機 Log。

## 對外 TCP 方法

- `DFU,<device_no>,<KVM_IP>,input,1:SN123,2:SN456`
- `DFU,<device_no>,<KVM_IP>,check`
- 通用 `stream` 工具保留。

`input` 的 payload 必須是以逗號分隔的 `slot:SN`。slot 可稀疏、輸入順序可任意，但程式會按 slot 由小到大輸入。空 payload、重複 slot、空 SN、格式錯誤及不在 profile 範圍內的 slot一律在送 HID 前拒絕。DFU 不支援對外 `button`；OK 已整合在 `input` 最後一步。

設備編號對應的版型只從 `~/Documents/template/device_profiles.json` 讀取，例如：

```json
{"DFU": {"1": "4slot", "2": "7slot"}}
```

僅接受 `4slot`、`7slot`。沒有對應、JSON 無效或 profile 不合法時 fail closed，不從畫面猜測槽數。

## 模板

正式模板根目錄為 `~/Documents/template/DFU/`：

- 主程式：`DFU_dock_icon.png`、`DFU_window.png`、`DFU_checkbox_checked.png`、`DFU_checkbox_unchecked.png`、`DFU_input.png`、`DFU_button.png`。
- 主程式 slot 錨點：`DFU_slot1_4slot.png`～`DFU_slot4_4slot.png`，以及 `DFU_slot1_7slot.png`～`DFU_slot7_7slot.png`。
- Log 視窗：`DFU_log_dock_icon.png`、`DFU_log_window.png`、`DFU_log_testing.png`、`DFU_log_pass.png`、`DFU_log_fail.png`、`DFU_log_notest.png`。
- Log slot 錨點：`DFU_log_slot1_4slot.png`～`DFU_log_slot4_4slot.png`，以及 `DFU_log_slot1_7slot.png`～`DFU_log_slot7_7slot.png`。

舊有通用 `slot_label`、`group_label` 不再是可製作或執行時讀取的模板。

## Input 狀態機

1. 取影格，以 `DFU_dock_icon.png` 定位並用絕對滑鼠點擊，等待 0.3 秒。
2. 重新取影格，定位 DFU window、profile 專屬 slot 錨點、checkbox、input 與 button。
3. payload 有 SN 的 slot 必須 checked；其餘 profile slot 必須 unchecked。
4. 每槽分別匹配 checked/unchecked；最高分必須至少 0.80，且高於另一狀態至少 0.05。
5. 只點擊狀態不符的 checkbox，等待 0.3 秒再以新影格逐槽複驗；仍不符的槽只重試一次。
6. 第二次複驗不符或狀態不明確即停止，絕不輸入 SN 或點 OK。
7. 以複驗影格重新定位 input/button，點 input，依 slot 升冪逐筆輸入 SN，每筆後按 Enter。
8. 所有 SN 都填入後只點一次 OK，接著送 `Command+Shift+M`，成功才回覆 `action_done`。

## Check 狀態機

1. 取影格，匹配並點擊 `DFU_log_dock_icon.png`，等待 0.3 秒。
2. 重新取影格，定位 Log window 與 profile 專屬 Log slot 錨點。
3. 每一列錨點右側 ROI 分別匹配 Testing、PASS、FAIL、Notest 並 OCR 該列 SN；狀態同樣要求分數至少 0.80、領先至少 0.05。
4. 任一有效 slot 為 Testing 時回覆 `action_done,testing`。
5. 全部完成時回覆所有 profile slot，例如 `action_done,1:SN123:pass,2::notest,3:SN789:fail`。PASS/FAIL 沒有有效 OCR SN 或狀態不明確時回覆錯誤，不能默認 Notest。

每次 input/check 都會覆寫 `~/Documents/template/_captures/match_diagnostics/DFU/latest/` 的最近一次診斷資料，可由主畫面「匹配結果」查看。所有失敗均 fail closed。
