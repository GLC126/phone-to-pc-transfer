@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0lan_share.py" %*
) else (
    py "%~dp0lan_share.py" %*
)
if errorlevel 1 pause
