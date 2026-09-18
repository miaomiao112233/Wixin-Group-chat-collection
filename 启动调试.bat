@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
rem WxSum debug launcher (console visible)
".venv\Scripts\python.exe" run.py 2>>logs\gui_stderr.log
if errorlevel 1 pause
