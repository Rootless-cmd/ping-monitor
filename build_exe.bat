@echo off
chcp 65001 >nul
title Ping Monitor - 打包构建

:: ============================================================
::  Ping Monitor  Windows 打包脚本
::  使用方法：双击此文件，或在命令行运行
:: ============================================================

echo.
echo  ██████╗ ██╗  ██╗██╗   ██╗     ███████╗████████╗██████╗ ██╗   ██╗██╗  ██╗
echo  ██╔══██╗██║  ██║╚██╗ ██╔╝     ██╔════╝╚══██╔══╝██╔══██╗██║   ██║╚██╗██╔╝
echo  ██████╔╝███████║ ╚████╔╝█████╗  ███████╗   ██║   ██║  ██║██║   ██║ ╚███╔╝
echo  ██╔═══╝ ██╔══██║  ╚██╔╝ ╚════╝  ╚════██║   ██║   ██║  ██║██║   ██║ ██╔██╗
echo  ██║     ██║  ██║   ██║          ███████║   ██║   ██████╔╝╚██████╔╝██╔╝ ██╗
echo  ╚═╝     ╚═╝  ╚═╝   ╚═╝          ╚══════╝   ╚═╝   ╚═════╝  ╚═════╝ ╚═╝  ╚═╝
echo.
echo  Windows 打包脚本
echo  ================================================================
echo.

:: ---------- 1. 检查 Python ----------
echo [1/4] 检查 Python 环境...
where python >nul 2>&1
if errorlevel 1 (
    echo  ✖ 未找到 Python，请先安装 Python 3.9+
    echo    官网：https://www.python.org/downloads/
    echo.
    echo 按任意键退出...
    pause >nul
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo     %PY_VER%  ✓

:: ---------- 2. 安装依赖 ----------
echo.
echo [2/4] 安装依赖 flet...
pip install flet -q
if errorlevel 1 (
    echo  ✖ flet 安装失败，尝试使用管理员权限...
    pip install flet -q --user
)
echo     安装完成 ✓

:: ---------- 3. 打包 ----------
echo.
echo [3/4] 开始打包（使用 PyInstaller）...
echo     这可能需要 3-10 分钟，请耐心等待...
echo.

:: 进入脚本所在目录
cd /d "%~dp0"

:: 使用 flet pack 打包（底层即 PyInstaller）
flet pack ping_monitor.py ^
    --name "PingMonitor" ^
    --product-name "Ping Monitor" ^
    --file-description "Ping 延迟与丢包监测工具" ^
    --company-name "PingMonitor" ^
    --copyright "Copyright (c) 2026" ^
    --product-version "1.0.0" ^
    --file-version "1.0.0.0" ^
    -y

if errorlevel 1 (
    echo.
    echo  ✖ 打包失败，请检查上方错误信息。
    echo.
    echo 按任意键退出...
    pause >nul
    exit /b 1
)

:: ---------- 4. 收尾 ----------
echo.
echo [4/4] 打包完成 ✓
echo.
echo  ================================================================
echo   打包成功！输出目录：
echo   dist\PingMonitor\PingMonitor.exe
echo  ================================================================
echo.
echo 按任意键打开输出目录...
pause >nul
start explorer "%~dp0dist\PingMonitor"
