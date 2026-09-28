@echo off
title 交通运输学院足球嘉年华档案管理系统 - 启动器
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "交运杯足球嘉年华管理系统.py"
) else (
    start "" python "交运杯足球嘉年华管理系统.py"
)
exit
