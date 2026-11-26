@echo off
title 停止 星图画廊
cd /d "%~dp0.."
echo 正在关闭 星图画廊 的界面服务...

set "LPID="
if exist "logs\server.pid" set /p LPID=<"logs\server.pid"
if defined LPID taskkill /f /pid %LPID% >nul 2>&1
if exist "logs\server.pid" del /f /q "logs\server.pid" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*DouK-Lite*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do taskkill /f /pid %%p >nul 2>&1
echo.
echo 已停止。端口 8765 现在应该是空的了。
ping -n 3 127.0.0.1 >nul
