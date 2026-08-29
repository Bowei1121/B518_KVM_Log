#!/bin/bash
# ============================================================
#  在 Mac 上一鍵打包 ui_app.py 成 .app
#  使用方式:
#    1. 把整個資料夾 (含 ui_app.py / target.png / requirements.txt) 拷到 Mac
#    2. 在「終端機」執行:  chmod +x build_mac.command
#    3. 直接雙擊 build_mac.command  (或在終端機執行 ./build_mac.command)
#  完成後在 dist/ 內會看到 AtlasTest.app
# ============================================================
set -e

# 切換到此腳本所在資料夾
cd "$(dirname "$0")"

echo "==> 使用的 Python:"
python3 --version
which python3

echo "==> 建立虛擬環境 .venv (若不存在)"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "==> 安裝相依套件"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> 清除舊的打包結果"
rm -rf build dist AtlasTest.spec

echo "==> 開始用 PyInstaller 打包 (.app, 單一檔案, 視窗模式)"
# 注意: macOS 的 --add-data 分隔符號是冒號 ":"
pyinstaller --noconfirm --windowed --onefile \
    --name AtlasTest \
    --add-data "FCT:FCT" \
    ui_app.py

echo ""
echo "============================================================"
echo " 完成! 執行檔在:  dist/AtlasTest.app"
echo " 第一次執行請參考 MAC_BUILD_README.md 的權限設定章節"
echo "============================================================"
