@echo off
chcp 65001 >nul
title 設定自動更新（一次性）
REM 在主機上雙擊執行一次，即可註冊「每 5 分鐘自動更新並重開服務」的工作排程。
REM 之後跨裝置推到 GitHub main 的更新，主機都會自動同步，你不必在場。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-autoupdate.ps1"
