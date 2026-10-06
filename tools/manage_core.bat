@echo off
REM ============================================================
REM  B518 JetKVM Relay Core Service Management Utility (Windows)
REM  Usage: manage_core.bat [start|stop|restart|status|force-kill]
REM ============================================================
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%~dp0manage_core.py" %*
