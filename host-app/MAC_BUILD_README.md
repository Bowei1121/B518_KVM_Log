# 在 Mac mini (Catalina 10.15.6, Intel) 打包與執行 AtlasTest

> ⚠️ **重點:Mac 執行檔無法在 Windows 上建立**，PyInstaller 不能跨平台編譯。
> 請務必**在這台 Mac mini 上**執行以下步驟（在哪台 Mac 建置，就最適合在那台跑）。

---

## 一、需要拷貝到 Mac 的檔案

把整個資料夾拷到 Mac（例如放到「文件」），至少包含：

- `ui_app.py`
- `requirements.txt`
- `build_mac.command`

---

## 二、安裝 Python（建議）

Catalina 內建的 Python 不適合打包 GUI。請到 <https://www.python.org/downloads/macos/>
下載 **Python 3.9.x（Intel/64-bit）** 安裝。安裝後確認：

```bash
python3 --version      # 應顯示 3.9.x
```

---

## 三、一鍵打包

打開「終端機」，切到資料夾後執行：

```bash
chmod +x build_mac.command
./build_mac.command
```

（或設定執行權限後直接「雙擊」`build_mac.command`）

腳本會自動：建立虛擬環境 → 安裝 opencv / numpy / pyautogui / mss / pyinstaller →
用 PyInstaller 打包。完成後執行檔在：

```
dist/AtlasTest.app
```

---

## 四、建立 JetKVM 視覺模板

影像比對是**比對畫面像素**。啟動 App 後按「擷取影像」再按「創建Pattern」，在視窗中選擇設備與模板種類並框選。正式模板會自動存入 `~/Documents/template/<DEVICE>/`，例如 `FCT_window.png`；模板不會被打包，更新後不需要重新執行打包。

---

## 五、第一次執行的系統權限（Catalina 很重要）

這個程式會「截圖」和「控制滑鼠鍵盤」，Catalina 會擋，需要手動授權：

1. 開啟 **系統偏好設定 → 安全性與隱私 → 隱私權**。
2. 左側選 **螢幕錄製 (Screen Recording)** → 勾選 `AtlasTest`（截圖比對需要）。
3. 左側選 **輔助使用 (Accessibility)** → 勾選 `AtlasTest`（pyautogui 移動/點擊滑鼠需要）。
4. 授權後**完全結束並重新開啟** `AtlasTest.app`，權限才會生效。

> 若雙擊出現「無法打開，因為來自未識別的開發者」：
> 在 `AtlasTest.app` 上**按右鍵 → 打開 → 打開**，或到
> 安全性與隱私 → 一般 → 「仍要打開」。

---

## 六、常見問題

- **打包成功但一打開就閃退**：多半是少了某個套件。
  可改用「終端機」直接執行內部的執行檔看錯誤訊息：
  ```bash
  ./dist/AtlasTest.app/Contents/MacOS/AtlasTest
  ```
- **辨識不到**：確認已用 Mac 上的 JetKVM 影格建立相應設備的正式模板（第四點）。
- **點擊沒反應**：通常是「輔助使用」權限沒給或沒重開 App。
- **想要更小/開很快的版本**：把 `build_mac.command` 裡的 `--onefile` 拿掉，
  會改成 `dist/AtlasTest.app`（資料夾形式），啟動較快但體積較大。
