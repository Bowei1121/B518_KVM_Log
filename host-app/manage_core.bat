@echo off
REM ============================================================
REM  B518 JetKVM Relay Core Service Management Utility (Windows)
REM ============================================================
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%~dp0..\tools\manage_core.py" %*
