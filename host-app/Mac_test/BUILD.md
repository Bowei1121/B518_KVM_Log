# Mac 滑鼠/鍵盤測試工具 — 在 Mac 上執行 / 打包說明

> ⚠️ 這是 macOS App,**無法在 Windows 上 build**(PyInstaller/py2app 不能跨平台
> 編譯 Mac 執行檔)。請把整個 `Mac_test` 資料夾複製到 Mac(mac mini 或任一台 Mac)
> 上,依下列步驟操作。

---

## 一、先安裝 Python 與套件(在 Mac 上)

macOS 內建 python3;若沒有,到 https://www.python.org 下載 Python 3。
在「終端機 (Terminal)」切到此資料夾後:

```bash
cd Mac_test
python3 -m pip install -r requirements.txt
```

(pyautogui 在 macOS 會一併安裝 pyobjc 相關套件)

---

## 二、直接執行(最快,先確認功能)

```bash
python3 mac_tool.py
```

會跳出視窗:輸入 X/Y 座標移動/點擊滑鼠、輸入文字/按 Enter。

### ⚠️ 一定要給「輔助使用 (Accessibility)」權限
macOS 為安全會擋住程式控制滑鼠鍵盤。第一次執行控制動作時會被擋、或跳權限請求。
請到:**系統設定 → 隱私權與安全性 → 輔助使用 (Accessibility)** →
把「**終端機 (Terminal)**」(用終端機執行時)或打包後的「**MacTool.app**」勾選開啟。
開啟後**完全結束再重新執行**才會生效。

> 緊急中止:程式有開 FAILSAFE,把滑鼠快速移到螢幕**左上角 (0,0)** 會強制中止。

---

## 三、打包成 .app(要雙擊執行、或給別台 Mac 用)

### 方法 A:PyInstaller(簡單)

```bash
python3 -m pip install pyinstaller
python3 -m PyInstaller --noconfirm --windowed --name MacTool mac_tool.py
```

產出:`dist/MacTool.app`。雙擊執行。
(第一次一樣要到「輔助使用」把 `MacTool` 勾選,並重開。)

### 方法 B:py2app(較正式的 .app)

```bash
python3 -m pip install py2app
python3 setup.py py2app          # 需自行建立 setup.py, 見下方範本
```

`setup.py` 範本:
```python
from setuptools import setup
setup(
    app=["mac_tool.py"],
    setup_requires=["py2app"],
    options={"py2app": {"packages": ["pyautogui"]}},
)
```
產出:`dist/mac_tool.app`。

---

## 四、Gatekeeper（未簽章 App 打不開時）

未簽章的 App 第一次雙擊可能出現「無法打開,因為來自未識別的開發者」:
- 在 `MacTool.app` 上**按右鍵 → 打開 → 打開**,或
- 系統設定 → 隱私權與安全性 → 一般 → 對該 App 按「**仍要打開**」。

若要免除此提示,需要 Apple 開發者帳號做**簽章 (codesign)**,一般內部測試用右鍵打開即可。

---

## 功能對照
| 動作 | 說明 |
|------|------|
| 讀取目前座標 | 把目前滑鼠位置填入 X/Y(方便找座標) |
| 移動滑鼠 | 移到 X/Y |
| 左鍵點擊 / 雙擊 | 在 X/Y 點擊 |
| 輸入文字 | 把文字打到目前焦點處 |
| 按 Enter | 送出 Enter |
| 輸入文字 + Enter | 打字後直接按 Enter |
