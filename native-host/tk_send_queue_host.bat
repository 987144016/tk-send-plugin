@echo off
REM Fallback only — Install-NativeHost registers tk_send_queue_host.exe (no console).
set HOST=%~dp0tk_send_queue_host.py
set PYW=%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\pythonw.exe
if exist "%PYW%" (
  "%PYW%" "%HOST%"
  exit /b %ERRORLEVEL%
)
where pythonw >nul 2>&1
if not errorlevel 1 (
  pythonw "%HOST%"
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 exit /b 1
  py -3 "%HOST%"
  exit /b %ERRORLEVEL%
)
python "%HOST%"
