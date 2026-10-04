# OCR 開發環境一鍵準備（Windows）。在 repo 根目錄執行：
# powershell -ExecutionPolicy Bypass -File ocr\setup_env.ps1
# 建立 %USERPROFILE%\.venvs\zizi-exam，下載 PP-OCRv5 ONNX 模型到 ocr\models。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = if ($env:OCR_VENV) { $env:OCR_VENV } else { Join-Path $env:USERPROFILE ".venvs\zizi-exam" }
$Python = if ($env:OCR_SETUP_PYTHON) { $env:OCR_SETUP_PYTHON } else { "py" }
$PythonArgs = if ($env:OCR_SETUP_PYTHON) { @() } else { @("-3.11") }

try {
  & $Python @PythonArgs -c "import sys; assert sys.version_info[:2] == (3, 11)" 2>$null
} catch {
  throw "需要 Python 3.11。請先執行：winget install Python.Python.3.11"
}
if ($LASTEXITCODE -ne 0) { throw "需要 Python 3.11。請先執行：winget install Python.Python.3.11" }

if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
  & $Python @PythonArgs -m venv $Venv
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
$Requirements = Join-Path $env:TEMP "zizi-ocr-requirements.txt"
Get-Content (Join-Path $Root "ocr\requirements.txt") | Where-Object { $_ -notmatch "rapid-latex-ocr" } | Set-Content $Requirements
& $VenvPython -m pip install -r $Requirements python-docx rapidfuzz

$Models = Join-Path $Root "ocr\models"
$ServerModels = Join-Path $Models "server"
New-Item -ItemType Directory -Force -Path $ServerModels | Out-Null
$BaseUrl = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5"
function Get-Model([string]$Remote, [string]$Destination) {
  if ((Test-Path $Destination) -and ((Get-Item $Destination).Length -gt 0)) { return }
  Invoke-WebRequest -Uri "$BaseUrl/$Remote" -OutFile $Destination
}
Get-Model "det/ch_PP-OCRv5_det_mobile.onnx" (Join-Path $Models "det.onnx")
Get-Model "rec/ch_PP-OCRv5_rec_mobile.onnx" (Join-Path $Models "rec.onnx")
Get-Model "det/ch_PP-OCRv5_det_server.onnx" (Join-Path $ServerModels "det.onnx")
Get-Model "rec/ch_PP-OCRv5_rec_server.onnx" (Join-Path $ServerModels "rec.onnx")
if (Test-Path (Join-Path $Models "ppocrv5_dict.txt")) {
  Copy-Item (Join-Path $Models "ppocrv5_dict.txt") (Join-Path $ServerModels "ppocrv5_dict.txt") -ErrorAction SilentlyContinue
}

& $VenvPython -c "import rapidocr_onnxruntime, cv2, fitz, docx, rapidfuzz, opencc; print('Python 套件 OK')"
Write-Host "完成：$Venv"
Write-Host "另請安裝 Poppler（需提供 pdftotext；OCR 轉圖也需要 pdftoppm），並將其 bin 加入 PATH。"
