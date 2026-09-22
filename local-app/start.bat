@echo off
chcp 65001 >nul
REM 詳解本機應用 一鍵啟動（Windows）。放在 local-app\ 底下，雙擊即可。
cd /d "%~dp0.."

where node >nul 2>nul
if errorlevel 1 (
  echo [錯誤] 找不到 Node.js。請先安裝 LTS 版：https://nodejs.org/
  pause
  exit /b 1
)

if not exist node_modules (
  echo 首次啟動，安裝相依套件（docx）...
  call npm install
)

echo.
echo 啟動中... 啟動後請用瀏覽器開 http://localhost:8787
echo 其他裝置（Tailscale）請開 http://主機的Tailscale名稱:8787
echo 關閉此視窗即停止服務。
echo.

REM 引擎預設 claude-code（需本機已安裝並登入 Claude Code CLI）。
REM 若要改用 API，把上面那行註解拿掉、填入金鑰，並把下面 npm start 前加 set：
REM set ENGINE=api
REM set ANTHROPIC_API_KEY=sk-ant-...

call npm start
pause
