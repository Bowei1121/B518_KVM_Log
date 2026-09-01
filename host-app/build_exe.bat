@echo off
chcp 65001 >nul
REM ============================================================
REM  Build ui_app.py -> exe (onedir)
REM  Output: dist\AtlasKVM\AtlasKVM.exe
REM  Templates stay external at %%USERPROFILE%%\Documents\template
REM ============================================================
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --windowed --name AtlasKVM --collect-all rapidocr_onnxruntime --collect-all onnxruntime --collect-all av --collect-all aiortc --collect-all cv2 ui_app.py

echo.
echo ============================================================
echo  Done. exe at dist\AtlasKVM\AtlasKVM.exe
echo  Templates are external: %%USERPROFILE%%\Documents\template
echo ============================================================
pause
