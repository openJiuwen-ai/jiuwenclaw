@echo off
REM JiuwenClaw 打包 exe 脚本
REM 用法: Scripts\build-exe.bat  或双击运行

cd /d "%~dp0\.."

echo === JiuwenClaw 打包 exe ===
echo.

echo [1/3] 安装 Python 依赖...
call uv sync --extra dev
if errorlevel 1 exit /b 1

echo.
echo [2/3] 构建前端...
cd jiuwenclaw\web
call npm install
if errorlevel 1 (cd ..\.. & exit /b 1)
call npm run build
if errorlevel 1 (cd ..\.. & exit /b 1)
cd ..\..

echo.
echo [3/3] 执行 PyInstaller 打包...
call uv run pyinstaller Scripts\jiuwenclaw.spec
if errorlevel 1 exit /b 1

echo.
echo === 打包完成 ===
echo exe 位置: %cd%\dist\jiuwenclaw.exe
pause
