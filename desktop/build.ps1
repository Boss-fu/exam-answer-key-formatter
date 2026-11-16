# Builds ZiziOCR desktop (Windows). ASCII only: Windows PowerShell 5.1 misreads UTF-8 without BOM.
# Usage: build.ps1 -Repo <exam-answer-key-formatter checkout> -Work <work folder>
# Needs Python 3.11 with ocr/requirements.txt (without rapid-latex-ocr), python-docx, rapidfuzz, openpyxl, pystray, pyinstaller.
# ocr/models/det.onnx + rec.onnx must exist, and rapid_layout must already hold pp_doc_layoutv3.onnx (run crop.py once).
param(
  [Parameter(Mandatory)] [string] $Repo,
  [Parameter(Mandatory)] [string] $Work,
  [string] $Python = "python"
)
$ErrorActionPreference = "Continue"   # PyInstaller logs to stderr; failure is checked via LASTEXITCODE
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Ocr = $Repo
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name ZiziOCR `
  --distpath "$Work\dist" --workpath "$Work\build" --specpath "$Work" `
  --paths "$here" --paths "$Ocr\ocr" --paths "$Ocr\ocr\figcrop" --paths "$Ocr\exam-to-word\scripts" `
  --hidden-import hostlib --hidden-import tray --hidden-import selftest --hidden-import pystray --hidden-import pystray._win32 `
  --hidden-import pyexam --hidden-import to_zizi_word --hidden-import to_qbank_xlsx --hidden-import buildlib --hidden-import ppocr_engine --hidden-import crop --hidden-import refine --hidden-import ocr_reuse `
  --collect-all rapidocr_onnxruntime --collect-all rapid_layout --collect-data opencc --collect-data docx --collect-binaries onnxruntime `
  --add-data "$Ocr\ocr\models\det.onnx;models" --add-data "$Ocr\ocr\models\rec.onnx;models" `
  --add-data "$Ocr\ocr\models\ppocrv5_dict.txt;models" --add-data "$Ocr\ocr\models\dehand.json;ocr\models" `
  --add-data "$Ocr\exam-to-word\assets\template.docx;assets" `
  --exclude-module torch --exclude-module tkinter --exclude-module matplotlib `
  "$here\zizi_host.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
if (-not (Test-Path "$Work\dist\ZiziOCR\_internal\rapid_layout\models\pp_doc_layoutv3.onnx")) { throw "layout model pp_doc_layoutv3.onnx not bundled" }
Copy-Item "$here\README-desktop.txt" "$Work\dist\ZiziOCR\README.txt" -ErrorAction SilentlyContinue
Write-Host "BUILD OK: $Work\dist\ZiziOCR\ZiziOCR.exe"
