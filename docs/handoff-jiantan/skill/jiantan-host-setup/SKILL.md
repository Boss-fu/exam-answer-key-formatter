---
name: jiantan-host-setup
description: 在「劍潭」這台 Windows 電腦安裝茲茲主機背景服務（OCR 主機＋AI 橋接＋Tailscale HTTPS），讓補習班老師透過 Tailscale 連到這台電腦做考卷 OCR 與 AI 分類。當擁有者說「幫我設定劍潭主機」「安裝茲茲主機背景服務」「把這台電腦變成 OCR 主機」時使用。
---

# 劍潭主機安裝（給這台電腦上的 Claude 照著做）

你正在**劍潭這台 Windows 電腦**上協助電腦擁有者（茲茲自然題庫 science-qbank 的擁有者）。目標：把這台電腦變成老師們的 OCR 運算主機＋AI 分類橋接，**一次安裝、之後在背景自動執行（沒有黑視窗）**。

## 先講清楚的規則
- 這台電腦的 Tailscale 維持**擁有者自己的帳號**，**不要**登入別人的帳號、**不要**改成別人的網路。老師透過 Tailscale「裝置分享」連進來。
- 每個會改變電腦的步驟（安裝軟體、建立排程、停用舊排程），先用一兩句話告訴擁有者要做什麼，再做。
- **絕對不要**使用 `tailscale funnel`、不要把任何埠轉發到公開網際網路、不要印出或貼出任何 token／金鑰／連線碼。
- 不確定的事就標「需人工確認」，不要猜、不要自己改設計。
- 本安裝程式在 Linux 測試過程式邏輯，**Windows 實機行為是第一次跑**。出錯就如實回報完整錯誤訊息，不要硬繞過去。

## 步驟

### 0. 現況盤點（只讀，不改東西）
在 PowerShell 執行並整理成表給擁有者看：
```powershell
winget --version; git --version; node -v; npm -v; py -0p; & "$env:ProgramFiles\Tailscale\tailscale.exe" status
Get-ScheduledTask | Where-Object { $_.TaskName -match '詳解|茲茲' } | Select TaskName,State
Get-NetTCPConnection -LocalPort 8787,8790,8443 -State Listen -ErrorAction SilentlyContinue | Select LocalPort,OwningProcess
```
回報：哪些已裝、哪些缺、這台是否已有舊的「詳解本機應用 自動更新」排程、8787／8790／8443 埠有沒有被占用。

### 1. 補齊前置軟體（缺什麼補什麼，先問擁有者）
```powershell
winget install --id Git.Git -e --source winget
winget install --id OpenJS.NodeJS.LTS -e --source winget
winget install --id Python.Python.3.11 -e --source winget     # 必須 3.11（rapidocr 不支援 3.13 以上）
winget install --id Tailscale.Tailscale -e --source winget    # 已裝就略過；用擁有者自己的帳號登入
```
安裝後**關掉並重開 PowerShell**，再驗證 `git -v`、`node -v`、`py -3.11 --version`。

### 2. 取得程式碼
- 位置：`C:\詳解應用`（**不要放在 Dropbox／OneDrive 裡**）。
- 這個專案是私有 repo：`https://github.com/Boss-fu/exam-answer-key-formatter`。
```powershell
git clone https://github.com/Boss-fu/exam-answer-key-formatter C:\詳解應用
cd C:\詳解應用
git pull --ff-only
Test-Path .\local-app\install-host-service.ps1
```
- 若 clone 被拒絕（沒有權限）：**停下來**，告訴擁有者「需要請 Boss-fu 把你的 GitHub 帳號加成這個 repo 的協作者，或請他提供整個資料夾的壓縮檔」，不要想辦法繞過。
- 若 `Test-Path` 是 False（安裝腳本還沒合併進 main）：`git fetch origin claude/manager-host; git checkout claude/manager-host`。之後 main 合併了就切回 main：`git checkout main; git pull --ff-only`。

### 3. OCR 的 Python 環境與模型
```powershell
cd C:\詳解應用
py -3.11 -m venv $env:USERPROFILE\.venvs\zizi-exam
$py = "$env:USERPROFILE\.venvs\zizi-exam\Scripts\python.exe"
(Get-Content ocr\requirements.txt) | Where-Object { $_ -notmatch 'rapid-latex-ocr' } | Set-Content $env:TEMP\ocr_req.txt
& $py -m pip install --upgrade pip
& $py -m pip install -r $env:TEMP\ocr_req.txt python-docx rapidfuzz openpyxl
& $py -c "import rapidocr_onnxruntime, cv2, pymupdf, docx, rapidfuzz, opencc, openpyxl; print('Python 套件 OK')"
```
PP-OCRv5 模型（`ocr\models\det.onnx`、`rec.onnx`；`ppocrv5_dict.txt` 已在 repo 內）：
```powershell
$B = 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5'
if (-not (Test-Path ocr\models\det.onnx)) { Invoke-WebRequest "$B/det/ch_PP-OCRv5_det_mobile.onnx" -OutFile ocr\models\det.onnx }
if (-not (Test-Path ocr\models\rec.onnx)) { Invoke-WebRequest "$B/rec/ch_PP-OCRv5_rec_mobile.onnx" -OutFile ocr\models\rec.onnx }
Get-ChildItem ocr\models
```
下載失敗就回報網址與錯誤，**不要換成別的來源**。

### 4. AI 橋接要用的 AI 工具
橋接會用這台電腦上**已登入**的命令列 AI（優先順序 agy → claude → codex）。檢查：
```powershell
Get-Command claude, codex, agy -ErrorAction SilentlyContinue
```
至少要有一個且已登入。告訴擁有者：老師用 AI 分類時，會消耗**這個帳號的額度**，同一時間只處理一題。沒有任何一個 → 回報，OCR 主機仍可裝，AI 分類晚點再補。

### 5. 安裝背景服務
```powershell
cd C:\詳解應用\local-app
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-host-service.ps1
```
（擁有者也可以直接雙擊 `安裝主機背景服務.bat`。）它會：註冊工作排程「茲茲主機背景服務」（登入時＋每 5 分鐘看門狗，隱藏視窗）、啟動 OCR 主機（8787→HTTPS 8443）和 AI 橋接（8790→HTTPS 8790）。若偵測到舊排程「詳解本機應用 自動更新」，會停用並記錄（可還原）。

### 6. 驗證
```powershell
cd C:\詳解應用\local-app
powershell -NoProfile -ExecutionPolicy Bypass -File .\host-service-status.ps1
curl.exe -s http://127.0.0.1:8787/api/health
curl.exe -s http://127.0.0.1:8790/engines -H "Origin: https://science-qbank.vercel.app"
```
預期：排程 Ready／Running、8787 與 8790 都「執行中」、health 回 `"service":"tzutzu-ocr"`、engines 列出可用的 AI 工具。再**關掉所有視窗**，確認服務還在；若能，請擁有者登出再登入（或重開機）一次，確認會自己起來。

### 7. Tailscale 後台（需要擁有者本人在瀏覽器操作，你只能引導）
請擁有者開 `https://login.tailscale.com/admin/dns`：
1. 確認 **MagicDNS** 已開。
2. 在 **HTTPS Certificates** 按 Enable。
然後你執行：
```powershell
& "$env:ProgramFiles\Tailscale\tailscale.exe" status --json | ConvertFrom-Json | % { $_.Self.DNSName }
& "$env:ProgramFiles\Tailscale\tailscale.exe" serve status
```
預期看到 `https://<這台名稱>.ts.net:8443` 與 `:8790` 兩個 HTTPS 入口。沒有的話看 `C:\詳解應用\local-app\output\host.log`（HTTPS 失敗通常是後台沒開 HTTPS 憑證，開了之後 1 分鐘內程式會自動重試）。
接著請擁有者在後台 **Machines → 這台電腦 → Share**，產生分享連結（給老師用，老師用自己的 Tailscale 帳號開連結、按 Accept）。**分享連結只給擁有者本人，你不要貼到任何公開地方。**

### 8. 回報（整理成下面這張表給擁有者，並請他轉給管理者）
| 項目 | 結果 |
|---|---|
| 這台電腦的 Tailscale 名稱 | `xxxx.xxxx.ts.net` |
| OCR 主機網址 | `https://<名稱>:8443` |
| AI 橋接網址 | `https://<名稱>:8790` |
| 排程狀態／開機自啟 | |
| 可用的 AI 工具 | agy／claude／codex |
| 失敗或需人工確認的項目 | |

管理者拿到「OCR 主機網址」後，在匯卷頁「OCR 運算主機」下拉選「劍潭」，第一次輸入這個網址即可。

## 出問題時
| 症狀 | 處理 |
|---|---|
| `winget` 不存在 | 請擁有者從 Microsoft Store 更新「應用程式安裝程式」，或手動到官網安裝 |
| pip 裝套件失敗 | 確認用的是 Python 3.11；貼出完整錯誤，不要換版本硬裝 |
| 8787 被占用 | `Get-NetTCPConnection -LocalPort 8787` 找出是誰；舊的詳解視窗就是它，安裝腳本會關掉；其他程式請問擁有者 |
| 服務沒起來 | 看 `local-app\output\host.log`、`ocr.log`、`ai-bridge.log` 的最後 30 行 |
| HTTPS 入口沒出現 | Tailscale 後台沒開 HTTPS 憑證，或 Tailscale 沒連線 |
| 想還原 | 雙擊 `解除安裝主機背景服務.bat`（會移除排程並還原舊排程） |
