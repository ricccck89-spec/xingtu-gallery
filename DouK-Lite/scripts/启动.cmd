@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
set "LOGF=%ROOT%\logs\启动日志.txt"
>"%LOGF%" echo [%date% %time%] launcher start



call :port_up 8765
if errorlevel 1 goto lite_start
if not exist "%ROOT%\logs\server.build" goto lite_restart
fc /b "%ROOT%\logs\server.build" "%ROOT%\web\build.txt" >nul 2>&1
if errorlevel 1 goto lite_restart
>>"%LOGF%" echo lite already up
goto web
:lite_restart
>>"%LOGF%" echo build changed, restart lite
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do taskkill /f /pid %%p >nul 2>&1
call :wait_down 8765 12
if errorlevel 1 (
    >>"%LOGF%" echo WARN 端口没自己放开，按命令行精确收一次
    powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*DouK-Lite*server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
    call :wait_down 8765 8
)
:lite_start
>>"%LOGF%" echo launching lite server
start "" /b "%ROOT%\python\pythonw.exe" -u "%ROOT%\server.py" > "%ROOT%\logs\server.log" 2>&1
call :wait_port 8765 30
if errorlevel 1 (
    >>"%LOGF%" echo ERROR 界面服务没在 30 秒内监听 8765，请看 logs\server.log
    exit /b 1
)
goto web
:web

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

set "PROF=%LocalAppData%\DouK-Lite\ChromeApp"
>>"%LOGF%" echo chrome=%CHROME%
if defined CHROME (
    start "" "%CHROME%" --app=http://127.0.0.1:8765/ --user-data-dir="%PROF%" --no-first-run --no-default-browser-check
) else (
    >>"%LOGF%" echo WARN chrome not found, 用默认浏览器打开
    start "" "http://127.0.0.1:8765/"
)
ping -n 2 127.0.0.1 >nul
exit /b 0

:port_up
netstat -an | findstr "LISTENING" | findstr ":%~1" >nul 2>&1
exit /b %errorlevel%

:wait_port
set "WP_PORT=%~1"
set "WP_LEFT=%~2"
:wait_port_loop
call :port_up %WP_PORT%
if not errorlevel 1 exit /b 0
set /a WP_LEFT-=1
if "%WP_LEFT%"=="0" exit /b 1
ping -n 2 127.0.0.1 >nul
goto wait_port_loop

:wait_down
set "WD_PORT=%~1"
set "WD_LEFT=%~2"
:wait_down_loop
call :port_up %WD_PORT%
if errorlevel 1 exit /b 0
set /a WD_LEFT-=1
if "%WD_LEFT%"=="0" exit /b 1
ping -n 2 127.0.0.1 >nul
goto wait_down_loop
