# 一鍵重開服務（Windows）：關掉舊服務 → 拉 main 最新版 → 重新啟動 start.bat。
# 由 restart.bat 雙擊呼叫。只做安全的 fast-forward，本機有未提交修改時不覆蓋任何檔。
$ErrorActionPreference = 'Continue'
$port = 8787
$appDir = $PSScriptRoot                 # local-app
$repo = Split-Path $appDir -Parent      # repo 根
$startBat = Join-Path $appDir 'start.bat'

function Get-ServerPid {
  $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) { $c.OwningProcess }
}

function Stop-Server {
  $id = Get-ServerPid
  if (-not $id) { Write-Host '  沒有偵測到執行中的服務（可能本來就沒開）'; return }
  # 從 node 往上找到跑 start.bat 的 cmd 視窗一起關，避免留下停在 pause 的空視窗
  $ids = @($id)
  $cur = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
  for ($i = 0; $i -lt 5 -and $cur; $i++) {
    $cur = Get-CimInstance Win32_Process -Filter "ProcessId=$($cur.ParentProcessId)"
    if (-not $cur -or $cur.Name -notin @('cmd.exe', 'node.exe')) { break }
    $ids += $cur.ProcessId
    if ($cur.CommandLine -like '*start.bat*') { break }
  }
  Stop-Process -Id $ids -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  Write-Host '  已關閉舊服務'
}

Write-Host '===================================='
Write-Host '   詳解本機應用 . 一鍵重開服務'
Write-Host '===================================='

Set-Location -LiteralPath $repo
# 重新讀取系統＋使用者 PATH，避免繼承到裝 git/node 之前的舊環境
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$env:GIT_TERMINAL_PROMPT = '0'

Write-Host "`n[1/3] 更新程式碼（git 快轉到 main 最新版）..."
git fetch --quiet origin main 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host '  git fetch 失敗（網路或 GitHub 登入問題），略過更新，仍會重開服務'
} else {
  $dirty = git status --porcelain --untracked-files=no
  if ($dirty) {
    Write-Host '  本機有未提交修改，略過更新（不覆蓋你的檔）'
  } else {
    git merge --ff-only --quiet origin/main 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host '  已更新到最新版' } else { Write-Host '  無法快轉（本機與遠端分歧），略過更新' }
  }
}

Write-Host "`n[2/3] 關閉舊服務（埠 $port）..."
Stop-Server

Write-Host "`n[3/3] 重新啟動服務..."
Start-Process -FilePath $startBat -WorkingDirectory $appDir
Write-Host "`n完成！新的黑視窗會啟動服務，稍等幾秒後回瀏覽器重新整理（Cmd+Option+E 清快取再 Cmd+R）即可。"
Write-Host '（這個視窗會自動關閉。）'
Start-Sleep -Seconds 4
