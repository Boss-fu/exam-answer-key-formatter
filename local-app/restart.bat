@echo off
chcp 65001 >nul
title 詳解應用 一鍵重開服務
REM 雙擊即可：拉最新版 + 關掉舊服務 + 重新啟動。放在 local-app\ 底下。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart.ps1"
