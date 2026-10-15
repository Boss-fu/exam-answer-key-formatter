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
REM Windows 上 Node 無法直接 spawn "claude"（npm 只放了 .cmd/.ps1 包裝），改指向實際的 claude.exe。
if not defined CLAUDE_BIN if exist "%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe" set "CLAUDE_BIN=%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
REM 若要改用 API，把上面那行註解拿掉、填入金鑰，並把下面 npm start 前加 set：
REM set ENGINE=api
REM set ANTHROPIC_API_KEY=sk-ant-...
REM 若主機已安裝並登入 Codex CLI，改用：
REM set ENGINE=codex
REM 若 codex 不在 PATH，可加：set CODEX_BIN=C:\完整\路徑\codex.cmd
REM 理化詳解的 ChatGPT 對答案也用 Codex CLI；Codex App 內附的 codex.exe 在隨版本變動的資料夾，取最新一個。
if not defined CODEX_BIN if exist "%LOCALAPPDATA%\OpenAI\Codex\bin" for /f "delims=" %%D in ('dir /b /ad /o-d "%LOCALAPPDATA%\OpenAI\Codex\bin"') do if not defined CODEX_BIN if exist "%LOCALAPPDATA%\OpenAI\Codex\bin\%%D\codex.exe" set "CODEX_BIN=%LOCALAPPDATA%\OpenAI\Codex\bin\%%D\codex.exe"

REM 本機 OCR 試跑需要 Tesseract 與 Poppler 的 pdftoppm；有標準安裝位置時自動使用。
if not defined TESSERACT_BIN if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESSERACT_BIN=C:\Program Files\Tesseract-OCR\tesseract.exe"
if not defined PDFTOPPM_BIN if exist "C:\ocr-tools\poppler\Library\bin\pdftoppm.exe" set "PDFTOPPM_BIN=C:\ocr-tools\poppler\Library\bin\pdftoppm.exe"
REM Poppler 若解壓到不同位置，請把上面的 PDFTOPPM_BIN 改為實際 pdftoppm.exe 路徑。

call npm start
pause
