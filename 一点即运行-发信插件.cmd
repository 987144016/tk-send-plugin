@echo off
REM Silent: starts queue hidden (no black window). Registers Native Host if needed.
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\Install-NativeHost.ps1" > "%TEMP%\tk-send-install-nm.log" 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\Start-LocalQueue-Hidden.ps1"
echo [TK Send Plugin] queue start requested (hidden). Health: http://127.0.0.1:8767/api/health
echo Next: chrome://extensions -^> reload send-plugin -^> Options -^> 连接并启动
exit /b 0
