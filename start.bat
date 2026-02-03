@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title 图片管理工具

echo ========================================
echo        图片管理工具启动程序
echo ========================================
echo.

REM 设置虚拟环境路径
set "VENV_PATH=%~dp0imgM"
set "VENV_ACTIVATE=%VENV_PATH%\Scripts\activate.bat"

REM 检查虚拟环境是否存在
if not exist "%VENV_PATH%" (
    echo [错误] 虚拟环境目录不存在: %VENV_PATH%
    echo [提示] 请先创建虚拟环境: python -m venv imgM
    goto :error_exit
)

if not exist "%VENV_ACTIVATE%" (
    echo [错误] 虚拟环境激活脚本不存在: %VENV_ACTIVATE%
    echo [提示] 虚拟环境可能已损坏，请重新创建
    goto :error_exit
)

REM 激活虚拟环境 (使用 call 确保正确激活)
echo [1/4] 正在激活虚拟环境...
call "%VENV_ACTIVATE%"
if errorlevel 1 (
    echo [错误] 虚拟环境激活失败
    goto :error_exit
)
echo       虚拟环境已激活

REM 检查Python版本
echo [2/4] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.7+
    echo [提示] 访问 https://www.python.org/downloads/ 下载安装
    goto :error_exit
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo       Python版本: %%v

REM 检查并安装依赖
echo [3/4] 检查依赖包...
python -c "import flask; import PIL" >nul 2>&1
if errorlevel 1 (
    echo       正在安装缺失的依赖包...
    pip install flask pillow -q
    if errorlevel 1 (
        echo [错误] 依赖包安装失败
        goto :error_exit
    )
    echo       依赖包安装完成
) else (
    echo       依赖包已就绪
)

REM 检查主程序是否存在
if not exist "%~dp0image_manager.py" (
    echo [错误] 未找到主程序: image_manager.py
    goto :error_exit
)

REM 启动应用
echo [4/4] 启动应用程序...
echo.
echo ========================================
echo  访问地址: http://localhost:5000
echo  按 Ctrl+C 停止服务
echo ========================================
echo.

python "%~dp0image_manager.py"
set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [错误] 程序异常退出，退出代码: %EXIT_CODE%
    goto :error_exit
)

goto :end

:error_exit
echo.
echo 按任意键退出...
pause >nul
exit /b 1

:end
endlocal