@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare-ocr-env.ps1"
echo.
pause
