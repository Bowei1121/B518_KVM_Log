#!/bin/bash
# =====================================================================
#  build.sh  —  在 macOS 上自動把 mac_tool.py 打包成 MacTool.app
# ---------------------------------------------------------------------
#  用法 (在 Mac 的「終端機」裡):
#      cd Mac_test
#      chmod +x build.sh      # 第一次先給執行權限
#      ./build.sh
#
#  產出:  dist/MacTool.app   (雙擊即可執行)
#
#  ⚠️ 只能在 macOS 上執行, 無法在 Windows build Mac App。
# =====================================================================
set -e
cd "$(dirname "$0")"          # 切到腳本所在資料夾

APP_NAME="MacTool"
ENTRY="mac_tool.py"
VENV=".venv_build"

echo "==============================================="
echo "  打包 ${ENTRY}  ->  ${APP_NAME}.app"
echo "==============================================="

# 1) 確認 python3
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 找不到 python3。請先安裝 Python 3 (https://www.python.org)。"
    exit 1
fi
echo "• 使用 $(python3 --version)"

# 2) 建立乾淨的虛擬環境 (避免污染系統 Python)
echo "• 建立虛擬環境 ${VENV} ..."
python3 -m venv "${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

# 3) 安裝相依套件
echo "• 安裝 pyautogui / pyinstaller ..."
python -m pip install --upgrade pip >/dev/null
python -m pip install pyautogui pyinstaller

# 4) 清掉舊的產出
echo "• 清除舊的 build / dist ..."
rm -rf build dist "${APP_NAME}.spec"

# 5) 打包
echo "• 開始 PyInstaller 打包 ..."
python -m PyInstaller --noconfirm --clean --windowed \
    --name "${APP_NAME}" \
    "${ENTRY}"

deactivate

echo ""
echo "==============================================="
echo "✅ 完成!  產出: $(pwd)/dist/${APP_NAME}.app"
echo "==============================================="
echo ""
echo "接下來:"
echo "  1) 到 Finder 打開 dist/ , 雙擊 ${APP_NAME}.app"
echo "  2) 第一次會被 Gatekeeper 擋 -> 在 App 上按右鍵 → 打開 → 打開"
echo "  3) 系統設定 → 隱私權與安全性 → 輔助使用(Accessibility) 勾選 ${APP_NAME}"
echo "     (勾選後要完全結束 App 再重開才會生效, 否則滑鼠鍵盤控制沒作用)"
echo ""
