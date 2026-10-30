@echo off
REM Launch the existing silent starter, then exit this console immediately.
start "" /B wscript.exe //B //Nologo "%~dp0scripts\Start-LocalQueue-Silent.vbs"
exit /b 0
