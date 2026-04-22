@echo off
REM LumoAcq Windows 打包脚本 (onedir)
REM 输出: dist\LumoAcq\  把整个文件夹 copy 到目标机器即可 (目标机需预装 Galaxy SDK)

setlocal

set VENV_PY=.venv\Scripts\python.exe
if not exist %VENV_PY% (
    echo [ERR] 未找到虚拟环境 %VENV_PY%
    echo      先在项目根创建 .venv 并安装依赖
    exit /b 1
)

echo [1/3] 清理上次输出...
if exist build    rmdir /s /q build
if exist dist     rmdir /s /q dist

echo [2/3] 运行 PyInstaller...
%VENV_PY% -m PyInstaller build.spec --clean --noconfirm
if errorlevel 1 (
    echo [ERR] PyInstaller 失败
    exit /b %errorlevel%
)

echo [3/3] 打包完成
echo     产出: dist\LumoAcq\LumoAcq.exe
echo     大小:
dir /s /-c dist\LumoAcq | findstr "File(s)"

echo.
echo 部署说明:
echo   1. 目标 Windows 机器安装 Daheng Galaxy SDK (与本机同版本)
echo   2. 将 dist\LumoAcq\ 整个文件夹 copy 到目标机器
echo   3. 双击 LumoAcq.exe 即可运行

endlocal
