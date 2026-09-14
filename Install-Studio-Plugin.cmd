@echo off
cd /d "%~dp0"
python scripts\build_studio_plugin.py
if errorlevel 1 goto failed
powershell.exe -NoProfile -File scripts\install-studio-plugin.ps1
if errorlevel 1 goto failed
pause
exit /b 0
:failed
pause
exit /b 1
