@echo off
chcp 65001 >nul
cd /d "%~dp0"
python gdc2md.py %*
if "%~1"=="" pause
