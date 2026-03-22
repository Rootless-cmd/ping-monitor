@echo off
chcp 65001 >nul
title Ping Monitor

:: 检测本目录是否有虚拟环境
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0ping_monitor.py"
) else (
    :: 未找到虚拟环境，尝试系统 Python
    python "%~dp0ping_monitor.py"
)
pause
