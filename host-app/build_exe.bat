@echo off
chcp 65001 >nul
REM ============================================================
REM  Build AtlasKVM.exe and Core.exe from AtlasKVM.spec (onedir)
REM  Output: dist\AtlasKVM\AtlasKVM.exe
REM  Templates stay external at %%USERPROFILE%%\Documents\template
REM ============================================================
cd /d "%~dp0"

REM Version and build info for the title and Log (issue #115). Same step as CI; the build hash is the git short hash.
python "%~dp0..\tools\write_build_info.py"

python -m PyInstaller --noconfirm --clean AtlasKVM.spec

echo.
echo ============================================================
echo  Done. exe at dist\AtlasKVM\AtlasKVM.exe
echo  Templates are external: %%USERPROFILE%%\Documents\template
echo ============================================================
pause
