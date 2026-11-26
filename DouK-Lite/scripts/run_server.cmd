@echo off
title 星图画廊 · 后台服务（手动）
cd /d "%~dp0.."
if not exist "logs" mkdir "logs"
"%~dp0..\python\pythonw.exe" -u "%~dp0..\server.py" > "%~dp0..\logs\server.log" 2>&1
