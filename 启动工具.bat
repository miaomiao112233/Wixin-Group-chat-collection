@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
rem WxSum launcher
".venv\Scripts\pythonw.exe" run.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo [ERROR] GUI failed to start. Check logs\gui_stderr.log
echo Press any key to show details...
pause >nul
type logs\gui_stderr.log
pause
