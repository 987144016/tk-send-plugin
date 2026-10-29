@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [TK Send Plugin] register Native Messaging (Chrome 连接并启动 auto-starts queue)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-NativeHost.ps1"
if errorlevel 1 (
  echo Native host install had issues — extension may not auto-start queue.
)
echo.
echo [TK Send Plugin] starting local queue on 127.0.0.1:8767 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-LocalQueue.ps1"
if errorlevel 1 (
  echo Start failed. See scripts\Start-LocalQueue.ps1
  pause
  exit /b 1
)
echo.
echo Queue should be up. Next:
echo   1) Chrome already logged in as the Gmail in Options / config
echo   2) chrome://extensions -^> Reload send-plugin extension
echo   3) Options -^> 保存参数 -^> 连接并启动
echo After this once, 「连接并启动」auto-starts 8767 via Native Messaging — no manual cmd each time.
echo NO mass-send beyond configured pace. Feishu-only data. No workbench / pair codes.
echo Health: http://127.0.0.1:8767/api/health
pause
