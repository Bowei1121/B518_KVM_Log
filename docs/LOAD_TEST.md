# 錄影重播壓測（上線驗收）

對應 spec #1 的效能項目（user story 51–53）。以 `tools/replay_load_test.py` 驗收 Core Service 在九台設備同時長輪詢時的負載。

## 原理

- **模擬設備**：以錄好的 JetKVM 畫面序列（ReplayKVM）取代真實 KVM。模擬設備是設備清單中的一般設備，Core 用同一條判讀與長輪詢路徑處理它們。
- **混合真機**：`--include-real` 指定設備清單中的真實設備，連線方式與平常相同。
- **負載**：每台設備一個長輪詢用戶端（LabVIEW 的行為模型：`check` → 讀回覆 → 每輪結束後暫停 `--round-interval` 秒）。另有一個 `ping` 探測，量 Core 在負載下的回應速度。
- **量測**：Core 行程的 CPU（取樣平均與最大）、RSS（起點、峰值、終點）、回覆延遲、畫面年齡、`ping` 延遲。

## 錄影格式

錄影目錄包含 `frames.jsonl` 與 PNG 影格。每行一個 JSON 物件：

```json
{"file": "f00000.png", "pts": 100.0}
{"file": "f00001.png", "pts": 100.033}
```

- `pts` 為秒，必須嚴格遞增（即 WebRTC presentation timestamp）。
- 影格為 JetKVM 的完整畫面（BGR 色彩），須含完整的 Log Solution 狀態序列（待機 → 監控 → 完成 → 取用），才能產生本輪結果。
- 錄影來源目前不在本 repo 內。請先以擷取卡或既有的錄影流程產出上述格式；產出工具需另案規劃。

## 執行

```bash
python tools/replay_load_test.py --frames recordings/log_solution_30fps \
    --sim-count 6 --include-real DFU-01,FCT-01,BT-01 \
    --duration 1800 --report-dir reports
```

- `--sim-count`：模擬設備數（預設 6；編號從 `--sim-start` 開始，預設 61，避開真機編號）。
- 九台同時測試：真機 3 台加模擬 6 台，即驗收組合。
- 全部輸出寫入 `reports/replay-<時間>/report.json` 與 `report.md`。

## 30 分鐘驗收程序

1. 確認設備清單已儲存，Core Service 已重啟且無設備執行動作。
2. 確認錄影目錄可讀，`frames.jsonl` 的時間戳遞增（程式會檢查）。
3. 執行上方指令，`--duration 1800`，不要在過程中操作 Monitor UI 以外的程式。
4. 執行中可用 Monitor UI 手動檢查與快照，確認唯讀操作不影響 LabVIEW 模型的輪次（spec user story 42）。
5. 結束後檢閱 `report.md`：每一項都要「通過」。

## 判定標準

| 項目 | 門檻 | 說明 |
| --- | --- | --- |
| 內部錯誤 | 0 | 回覆出現 `internal_error` 即失敗 |
| 重播誤判凍結 | 0 | 重播畫面時間戳遞增，不應出現 `frame_frozen` |
| 畫面年齡 p95 | ≤ 0.5 秒 | Core 讀取時畫面的年齡 |
| `ping` 延遲 p95 | ≤ 0.2 秒 | 負載下 Core 的回應速度 |
| CPU 平均 | ≤ 70% | Core 行程（取樣平均） |
| RSS 成長 | ≤ 100 MB | 整段執行的記憶體成長（終點減起點） |

門檻定義於 `tools/replay_load_test.py` 的 `DEFAULT_LIMITS`。調整門檻須在報告中註明理由。

## 限制（報告時請一併說明）

- **測量位置**：Core 與用戶端、取樣器在同一行程內執行。用戶端只是輕量的 socket 讀寫，但 CPU 數字會包含它們。
- **畫面年齡**：ReplayKVM 是拉取式，畫面依播放時間計算，所以年齡永遠小於一個影格週期。它能偵測「畫面是否陳舊」，但不能反映 Core 處理延遲；處理延遲由回覆延遲反映。
- **模擬負載較輕**：模擬設備沒有 WebRTC 解碼成本。真機三台的 CPU 與記憶體必須以混合模式實測，不能只看純模擬結果。
- **判讀品質**：重播畫面可能產生與現場不同的判讀分佈。驗收重點是延遲、穩定與資源，不是判讀正確率。
