@echo off
chcp 65001 >nul
echo 正在启动图片管理工具...
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请先安装Python 3.7+
    echo 访问 https://www.python.org/downloads/ 下载安装
    pause
    exit /b 1
)

REM 检查并安装依赖
echo 检查依赖包...
pip install flask pillow >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖包...
    pip install flask pillow
)

REM 启动应用
echo.
echo 图片管理工具正在启动...
echo 访问地址: http://localhost:5000
echo 按 Ctrl+C 停止服务
echo.

python image_manager.py

if errorlevel 1 (
    echo.
    echo 启动失败，请检查以上错误信息
    pause
)