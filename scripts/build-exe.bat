@echo off
REM JiuwenSwarm build-exe script
REM Usage: scripts\build-exe.bat  or double-click to run

REM Delegate to the PowerShell build so both entry points share the
REM bundled Fixed Version WebView2 and Inno Setup packaging flow.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-exe.ps1" %*
exit /b %errorlevel%
