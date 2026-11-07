# 公司主機：低 token 考卷 OCR 從零設定

目標：先在公司 Windows 主機建立**本機 OCR 優先**環境。印刷文字由本機工具辨識；只有低信心文字、複雜圖表或筆跡遮住的區塊，才交給 Codex／Claude 或人工確認。

> 這份手冊不會把整份考卷自動送到 AI。現有網站的 Claude 視覺 OCR 是舊流程；完成本機環境後，網站才會改接這些工具。

## 最終分工

| 類型 | 優先工具 | AI 是否需要 |
| --- | --- | --- |
| 原生文字 PDF | PyMuPDF 直接取字 | 不需要 |
| 一般掃描中文、英文、題號、選項 | PaddleOCR | 不需要 |
| 快速備援與每字信心分數 | Tesseract | 不需要 |
| 雙欄、表格、公式、複雜版面 | MinerU | 不需要 token，但會用主機算力 |
| 模糊、手寫遮住、OCR 低信心區塊 | Codex／Claude 或人工 | 只處理局部 |

## 0. 先準備

- 用公司主機的**一般 PowerShell**開啟即可；只有安裝 Tesseract 或系統軟體時可能需要系統管理員權限。
- 主機需可上網，第一次安裝 Python 套件與下載 OCR 模型會較久。
- 不要把 repo 放在 Dropbox／OneDrive。既有位置 `C:\詳解應用` 可直接使用。

## 1. 確認既有網站與 Git

```powershell
cd C:\詳解應用
git status --short --branch
git pull --ff-only
node -v
npm -v
```

預期：`git status` 沒有未提交修改，且 `node -v`、`npm -v` 都有版本號。

若 Git、Node 尚未安裝，執行：

```powershell
winget install --id Git.Git -e --source winget
winget install --id OpenJS.NodeJS.LTS -e --source winget
```

安裝後關閉 PowerShell，重新開一個視窗，再重跑上面的驗證。

## 2. 安裝 Python 3.12 與建立隔離環境

Python 3.12 是 MinerU 目前建議的新環境版本。安裝：

```powershell
winget install --id Python.Python.3.12 -e --source winget
```

關閉並重開 PowerShell，確認：

```powershell
py -3.12 --version
```

建立 OCR 共用環境：

```powershell
New-Item -ItemType Directory -Force C:\ocr-tools | Out-Null
py -3.12 -m venv C:\ocr-tools\base
C:\ocr-tools\base\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

若 PowerShell 顯示「已停用指令碼執行」，不要永久修改系統設定；這個視窗改用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
C:\ocr-tools\base\Scripts\Activate.ps1
```

## 3. 安裝 Tesseract（必要、快速備援）

1. 從 [UB Mannheim 的 Windows 安裝版說明](https://github.com/tesseract-ocr/tesseract/wiki/Home) 安裝 64 位元 Tesseract。
2. 安裝位置維持預設：`C:\Program Files\Tesseract-OCR`。
3. 安裝程式若可選語言，勾選 **Traditional Chinese** 與 **English**。
4. 若沒有繁中語言檔，下載官方 `chi_tra.traineddata`，放入：
   `C:\Program Files\Tesseract-OCR\tessdata\`

重新開 PowerShell 後驗證：

```powershell
tesseract --version
tesseract --list-langs
```

清單必須至少含：`chi_tra`、`eng`。Tesseract 的 TSV／hOCR 都包含每個字的座標與信心分數，網站會用它判斷哪些區塊需要複核。

用一張真實掃描考卷測試：

```powershell
tesseract "C:\路徑\考卷.png" stdout -l chi_tra+eng --psm 6 tsv > C:\ocr-tools\tesseract-test.tsv
notepad C:\ocr-tools\tesseract-test.tsv
```

看到 `conf` 欄與辨識文字，即代表可用。

## 4. 安裝 Poppler（必要，讓 PDF 轉成 OCR 頁面圖）

網站的「OCR 試跑與覆核」會呼叫 `pdftoppm.exe`，把掃描 PDF 轉為 250 dpi PNG；它不使用 AI token。

1. 從 [Poppler 官方網站](https://poppler.freedesktop.org/) 的 Windows 連結下載可信任的 Windows 預編譯版（不要下載原始碼壓縮檔）。
2. 解壓後，將包含 `Library\bin\pdftoppm.exe` 的資料夾整理成：
   `C:\ocr-tools\poppler\Library\bin\pdftoppm.exe`
3. 不必修改全系統 PATH；本 repo 的 `local-app\start.bat` 會自動尋找這個位置。
4. 在 PowerShell 驗證：

```powershell
& "C:\ocr-tools\poppler\Library\bin\pdftoppm.exe" -h
```

看到說明文字即代表可用。

## 5. 安裝 PaddleOCR（必要、主要 OCR）

仍在已啟用的 `C:\ocr-tools\base` 環境內執行：

```powershell
python -m pip install paddlepaddle
python -m pip install paddleocr
```

驗證安裝：

```powershell
python -c "import paddleocr; print('PaddleOCR', paddleocr.__version__)"
paddleocr ocr -i "C:\路徑\考卷.png"
```

第一次執行會下載模型，請等它完成。一般 CPU 主機可先用這個版本；不必先碰 GPU/CUDA。

若需要表格、公式與版面解析，才加裝可選元件：

```powershell
python -m pip install "paddleocr[doc-parser]"
```

## 6. 安裝 MinerU（選配，建議在前五步成功後做）

MinerU 適合雙欄考卷、表格、公式與複雜 PDF，能輸出 JSON／Markdown；它在本機跑，不使用 AI token，但初次下載模型與 CPU 推論會花時間。

建立**獨立**環境，避免它的模型依賴和 PaddleOCR 互相干擾：

```powershell
py -3.12 -m venv C:\ocr-tools\mineru
C:\ocr-tools\mineru\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -U "mineru>=4.0,<5"
mineru version --json
```

用一份短 PDF 測試（先只跑第 1 頁）：

```powershell
New-Item -ItemType Directory -Force C:\ocr-tools\mineru-output | Out-Null
mineru parse "C:\路徑\考卷.pdf" --pages 1 -o C:\ocr-tools\mineru-output
```

若主機沒有 GPU，先接受 CPU 較慢；**不要**為了安裝 MinerU 先升級 CUDA 或強裝 GPU 套件。確認基礎 OCR 能用後，再視主機硬體另行優化。

## 7. 安裝並登入 Codex（只做補洞，不讀整卷）

```powershell
npm install -g @openai/codex
codex login
codex exec "Reply with exactly OK"
```

最後一行應只回 `OK`。網站引擎預設仍可用 Claude；之後若要主機改用 Codex，在 `C:\詳解應用\local-app\start.bat` 的 `call npm start` 前加入：

```bat
set ENGINE=codex
```

> 不要同時設 `ENGINE=claude-code` 與 `ENGINE=codex`；只保留一個。

## 8. 啟動網站並驗收

```powershell
cd C:\詳解應用
npm install
.\local-app\restart.ps1
```

瀏覽器開：

```text
http://localhost:8787
```

再從你的 Mac 用 Tailscale 網址驗證：

```text
http://ljm2222.tail3d3c18.ts.net:8787
```

## 明天的完成定義

- [ ] Git／Node／Python 3.12 都有版本號。
- [ ] `tesseract --list-langs` 有 `chi_tra`、`eng`。
- [ ] `pdftoppm.exe -h` 有顯示說明文字。
- [ ] PaddleOCR 對一張考卷圖片有輸出。
- [ ] MinerU（若安裝）成功解析一頁 PDF。
- [ ] Codex CLI 回覆 `OK`。
- [ ] 網站可從主機與 Tailscale 開啟。

完成前四項後，把每個驗證指令的輸出或截圖傳給 Codex；下一步才是把網站改為「PyMuPDF／PaddleOCR／Tesseract／MinerU 優先，低信心區塊才呼叫 AI」。

## 常見問題

| 狀況 | 處理 |
| --- | --- |
| `py -3.12` 找不到 | 關閉重開 PowerShell；仍失敗就到 Python 安裝程式勾選 Add Python to PATH。 |
| `tesseract` 找不到 | 將 `C:\Program Files\Tesseract-OCR` 加到系統 PATH，重開 PowerShell。 |
| 缺少 `chi_tra` | 將官方 `chi_tra.traineddata` 放進 `tessdata`，再執行 `tesseract --list-langs`。 |
| PaddleOCR 第一次很慢 | 正常，首次會下載模型；先用一頁測試，不要重複中斷。 |
| MinerU 很慢或安裝失敗 | 先跳過；Tesseract＋PaddleOCR 已能建立低 token 基線，MinerU 可稍後獨立處理。 |
| Codex 回報未登入 | 在主機執行 `codex login`，完成瀏覽器登入後再跑 `codex exec "Reply with exactly OK"`。 |
