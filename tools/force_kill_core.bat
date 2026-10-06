@echo off
REM ============================================================
REM  Emergency Force Kill Core Service (Windows)
REM ============================================================
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%~dp0manage_core.py" force-kill %*
pause
