@echo off
REM JiuwenAvatar 打包 exe 脚本（Windows Desktop）
REM 用法: scripts\build-exe.bat  或双击运行

cd /d "%~dp0\.."

echo === JiuwenAvatar 打包 exe (Windows Desktop) ===
echo.

echo [1/5] 安装 Python 依赖（含 PyInstaller、pywebview 等）...
call uv sync --extra dev
if errorlevel 1 exit /b 1

echo.
echo [2/5] 构建前端...
cd jiuwenavatar\channels\web\frontend
call npm install
if errorlevel 1 (cd ..\..\..\.. & exit /b 1)
call npm run build
if errorlevel 1 (cd ..\..\..\.. & exit /b 1)
cd ..\..\..\..

echo.
echo [3/5] 从 pyproject.toml 读取版本号...
for /f %%i in ('uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"') do set VERSION=%%i
echo   当前版本: %VERSION%

echo [4/5] 清理旧的构建产物...
if exist "dist\jiuwenavatar" rmdir /s /q "dist\jiuwenavatar"

echo.
echo [4/5] 执行 PyInstaller 打包（--clean --noconfirm）...
call uv run pyinstaller scripts\jiuwenavatar.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo.
echo [5/5] 尝试构建 Inno Setup 安装程序...
set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"
)
if defined ISCC (
    %ISCC% /DMyAppVersion="%VERSION%" scripts\installer.iss
    %ISCC% /DMyAppVersion="%VERSION%" scripts\jiuwenavatar.iss
) else (
    echo   Inno Setup 未安装，跳过安装包构建。
    echo   请从 https://jrsoftware.org/isdl.php 下载 Inno Setup 6 后重新运行。
)

echo.
echo === 打包完成 ===
echo 桌面版目录: %cd%\dist\jiuwenavatar
echo 主程序: %cd%\dist\jiuwenavatar\jiuwenavatar.exe
for %%I in ("%cd%\dist\JiuwenAvatar-setup-*.exe") do (
    if exist "%%I" echo 安装包: %%I
)
pause