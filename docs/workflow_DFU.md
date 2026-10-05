# DFU 設備工作流程

更新日期：2026-09-03（Asia/Shanghai）

本文件是 DFU 工作流程的單一來源。JetKVM 專案只以 HDMI 串流畫面判讀並經 USB HID 操作；本機 Log 的讀取仍由獨立專案負責。本 repo 只會送出 `Command+Shift+M` 快捷鍵要求該程式開始監控，不讀取或比對任何本機 Log。

## 對外 TCP 方法

- `DFU,<device_no>,<KVM_IP>,input,1:SN123,2:SN456`
- `DFU,<device_no>,<KVM_IP>,check`
- 通用 `stream` 工具保留。

`input` 的 payload 必須是以逗號分隔的 `slot:SN`。slot 可稀疏、輸入順序可任意，但程式會按 slot 由小到大輸入。空 payload、重複 slot、空 SN、格式錯誤及不在 profile 範圍內的 slot一律在送 HID 前拒絕。DFU 不支援對外 `button`；OK 已整合在 `input` 最後一步。

設備編號對應的外部 DFU 輸入版型只從 `~/Documents/template/device_profiles.json` 讀取，例如：

```json
{"DFU": {"1": "4slot", "2": "7slot"}}
```

僅接受 `4slot`、`7slot`。此設定只描述 DFU 輸入視窗的實體欄位，不代表 B518 Log Solution 的結果容量；沒有對應、JSON 無效或 profile 不合法時，輸入操作 fail closed，不從畫面猜測欄位數。

## 模板

正式模板根目錄為 `~/Documents/template/DFU/`：

- 主程式：`DFU_dock_icon.png`、`DFU_window.png`、`DFU_checkbox_checked.png`、`DFU_checkbox_unchecked.png`、`DFU_input.png`、`DFU_button.png`。
- 主程式 slot 錨點：`DFU_slot1_4slot.png`～`DFU_slot4_4slot.png`，以及 `DFU_slot1_7slot.png`～`DFU_slot7_7slot.png`。
Log 結果不再使用 DFU 專屬視窗或 slot 模板。上位機直接讀取 B518 Log Solution 的 JetKVM frame，依 App repo 的 `docs/refactoring/KVM_DISPLAY_CONTRACT.md`（版本 1.0）定位點、黑白狀態標記與最多二十格色帶判讀。兩 repo 維持獨立，透過版號契約及受控樣本同步。

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

1. 讀取 `JetKVMClient.latest_frame()` 的 BGR 影格、遞增序號與單調接收時間；過期、無序、裁切或無法定位畫面一律不取用。
2. 以兩個不對稱定位點確認方向與比例，再辨識 2×2 狀態標記及一至二十格結果色帶。容量由連續有效色格及其後容量外黑格判定，不讀取 4／7 格輸入 profile。
3. 每個設備需先在本連線觀察到「監控中」才會接受後續完成畫面。待確認回覆 `action_paused,review`；待命、監控中、未知或尚在確認一致性的畫面回覆 `action_waiting,...`。
4. 只有「本輪完成」標記、容量內全部為 PASS／FAIL／NOTEST／TIMEOUT，且兩張不同 frame sequence 的狀態與結果一致，才一次回覆 `action_done,1::PASS,2::FAIL,...`。這個畫面契約不含 SN，因此此介面只回傳位置與狀態，不聲稱 OCR 或 SN 追查已整合；外部 TCP 呼叫端仍待定位與相容確認。
5. 同一設備重複 check 不會重複取用。重連會清除監控 armed 狀態，必須重新看到監控中；各設備閘門互相隔離。結果回覆遺失後重複 check 只回 `action_waiting,already_taken`，不自動重做。

DFU input 仍會覆寫 `~/Documents/template/_captures/match_diagnostics/DFU/latest/` 的最近一次模板診斷資料，可由主畫面「匹配結果」查看。Round check 的可重現 frame 證據及逐格辨識報告由 `tools/verify_ticket16_app_frames.py` 產生至指定隔離輸出路徑；所有未知、逾時、定位失敗及格式不一致均 fail closed。實際 JetKVM 影格／壓縮容差尚未驗收。
