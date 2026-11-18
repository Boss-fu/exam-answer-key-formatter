# 準備 OCR 環境（Windows，只需做一次）：Python 3.11 虛擬環境＋套件＋PP-OCRv5 模型
# 位置固定在 %USERPROFILE%\.venvs\zizi-exam（主機程式會自動找這個位置）。重複執行是安全的。
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 的下載進度條會讓 Invoke-WebRequest 慢數十倍
$ProgressPreference = 'SilentlyContinue'
$repo = Split-Path $PSScriptRoot -Parent
function Say($m, $c = 'White') { Write-Host $m -ForegroundColor $c }

# 1) Python 3.11
$pyOk = $false
try { & py -3.11 --version 2>$null | Out-Null; $pyOk = ($LASTEXITCODE -eq 0) } catch {}
if (-not $pyOk) {
  Say '找不到 Python 3.11（必須是 3.11；rapidocr 不支援 3.13 以上）。請先安裝：' Red
  Say '  winget install --id Python.Python.3.11 -e --source winget' Yellow
  Say '裝好後關掉這個視窗，重新開一次再執行。' Yellow
  exit 1
}
Say 'Python 3.11：OK' Green

# 2) 虛擬環境與套件
$venv = Join-Path $env:USERPROFILE '.venvs\zizi-exam'
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) {
  Say "建立虛擬環境：$venv" Cyan
  & py -3.11 -m venv $venv
}
$req = Join-Path $env:TEMP 'zizi_ocr_req.txt'
(Get-Content (Join-Path $repo 'ocr\requirements.txt')) | Where-Object { $_ -notmatch 'rapid-latex-ocr' } | Set-Content -Encoding UTF8 $req
Say '安裝 Python 套件（第一次約需數分鐘）…' Cyan
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r $req python-docx rapidfuzz openpyxl --quiet
& $py -c "import rapidocr_onnxruntime, cv2, pymupdf, docx, rapidfuzz, opencc, openpyxl; print('Python 套件 OK')"
if ($LASTEXITCODE -ne 0) { Say '套件檢查失敗，請把上面的錯誤訊息貼給管理者。' Red; exit 1 }

# 3) 模型（ppocrv5_dict.txt 已在 repo 內；det/rec 需要下載）
$models = Join-Path $repo 'ocr\models'
New-Item -ItemType Directory -Force $models | Out-Null
$B = 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5'
foreach ($m in @(@('det/ch_PP-OCRv5_det_mobile.onnx', 'det.onnx'), @('rec/ch_PP-OCRv5_rec_mobile.onnx', 'rec.onnx'))) {
  $dst = Join-Path $models $m[1]
  if ((Test-Path $dst) -and (Get-Item $dst).Length -gt 1MB) { Say "$($m[1])：已存在" Green; continue }
  Say "下載 $($m[1]) …" Cyan
  try { Invoke-WebRequest -Uri "$B/$($m[0])" -OutFile $dst -UseBasicParsing } catch { Say "下載失敗：$($_.Exception.Message)" Red; Say '請確認這台電腦可以連網，或把這個錯誤貼給管理者。不要換成別的來源。' Yellow; exit 1 }
  if ((Get-Item $dst).Length -lt 1MB) { Remove-Item $dst -Force; Say "$($m[1]) 下載不完整，請重跑一次。" Red; exit 1 }
}
if (-not (Test-Path (Join-Path $models 'ppocrv5_dict.txt'))) { Say '缺少 ocr\models\ppocrv5_dict.txt（應該在安裝包內），請確認資料夾完整。' Red; exit 1 }
Say ''
Say 'OCR 環境準備完成。下一步：雙擊「安裝主機背景服務.bat」。' Green
