# 主機自動更新（Windows）：由工作排程器每 5 分鐘執行一次。
# - GitHub main 有新版 → 快轉更新（必要時 npm install）→ 重開服務
# - 服務沒在跑 → 把 start.bat 叫起來
# 只做 fast-forward；本機有未提交修改或分歧時只記錄，不覆蓋任何檔案。
# 紀錄寫在 local-app\output\auto-update.log。

$ErrorActionPreference = 'Continue'
$repo = Split-Path $PSScriptRoot -Parent
$port = 8787
$branch = 'main'
$logDir = Join-Path $PSScriptRoot 'output'
$log = Join-Path $logDir 'auto-update.log'
New-Item -ItemType Directory -Force $logDir | Out-Null

function Write-Log($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

function Get-ServerPid {
  $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) { $c.OwningProcess }
}

function Stop-Server {
  $id = Get-ServerPid
  if (-not $id) { return }
  # 由 node 往上找到 start.bat 的 cmd 視窗一起關，避免留下停在 pause 的空視窗
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
}

function Start-Server {
  Start-Process -FilePath (Join-Path $PSScriptRoot 'start.bat') -WorkingDirectory $PSScriptRoot -WindowStyle Minimized
}

Set-Location -LiteralPath $repo
# 重新讀取系統＋使用者 PATH，避免繼承到安裝 git/node 之前的舊環境
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$env:GIT_TERMINAL_PROMPT = '0'
$restart = $false

git fetch --quiet origin $branch 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Log "git fetch 失敗（網路或 GitHub 登入問題），本次略過更新"
} else {
  $local = git rev-parse HEAD
  $remote = git rev-parse "origin/$branch"
  git merge-base --is-ancestor HEAD "origin/$branch"
  $behind = ($LASTEXITCODE -eq 0)
  if ($local -ne $remote -and -not $behind) {
    Write-Log "本機有 GitHub 上沒有的提交（領先或分歧），略過更新"
  } elseif ($local -ne $remote) {
    $dirty = git status --porcelain --untracked-files=no
    if ($dirty) {
      Write-Log "本機有未提交修改，略過更新：$($dirty -join '; ')"
    } else {
      git merge --ff-only --quiet "origin/$branch" 2>$null
      if ($LASTEXITCODE -ne 0) {
        Write-Log "無法快轉到 origin/$branch（本機與遠端分歧），略過更新"
      } else {
        Write-Log "已更新 $($local.Substring(0,7)) -> $($remote.Substring(0,7))"
        $changed = git diff --name-only $local $remote
        if ($changed -contains 'package.json') {
          npm install --no-audit --no-fund 2>$null | Out-Null
          Write-Log "package.json 有變動，已執行 npm install（結束碼 $LASTEXITCODE）"
        }
        $restart = $true
      }
    }
  }
}

if ($restart -and (Get-ServerPid)) {
  Stop-Server
  Write-Log "重開服務"
}
if (-not (Get-ServerPid)) {
  Start-Server
  Write-Log "服務未在執行，已啟動 start.bat"
}
