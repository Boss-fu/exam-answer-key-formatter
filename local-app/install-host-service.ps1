# 茲茲主機背景服務 安裝（Windows）：一次設定，之後開機登入自動在背景執行（沒有視窗，不會被誤關）
# 包含：OCR 主機＋AI 橋接＋Tailscale HTTPS 入口。重複執行是安全的（會覆蓋舊設定）。
# 若這台電腦原本用「詳解本機應用 自動更新」＋start.bat，會先停用舊的（記錄起來，可用解除安裝腳本還原）。
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$repo = Split-Path $here -Parent
$sup  = Join-Path $here 'host-supervisor.js'
$out  = Join-Path $here 'output'
New-Item -ItemType Directory -Force $out | Out-Null
$taskName = '茲茲主機背景服務'
$oldTask  = '詳解本機應用 自動更新'

function Say($m, $c = 'White') { Write-Host $m -ForegroundColor $c }

# 1) 前置檢查
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { $p = Join-Path $env:ProgramFiles 'nodejs\node.exe'; if (Test-Path $p) { $node = $p } }
if (-not $node) { Say '找不到 Node.js。請先安裝 LTS 版（https://nodejs.org/），裝好後重新執行這個安裝。' Red; exit 1 }
Say "Node.js：$node" Green
$tsExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path $tsExe)) { Say '提醒：沒找到 Tailscale。請安裝並登入（https://tailscale.com/download），否則其他電腦連不到這台主機。' Yellow }
if (-not (Test-Path (Join-Path $repo 'node_modules'))) {
  Say '第一次安裝：下載相依套件（npm install）…' Cyan
  Push-Location $repo; & npm install --omit=dev; Pop-Location
}

# 2) 停用舊的自動更新排程、關掉正在跑的舊服務（避免兩份搶 8787 埠）
$old = Get-ScheduledTask -TaskName $oldTask -ErrorAction SilentlyContinue
if ($old) {
  Disable-ScheduledTask -TaskName $oldTask | Out-Null
  Set-Content -LiteralPath (Join-Path $out 'disabled-old-task.txt') -Value $oldTask -Encoding UTF8
  Say "已停用舊排程「$oldTask」（解除安裝時可還原）" Yellow
}
$c = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($c) {
  $procs = @($c.OwningProcess)
  $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)").ParentProcessId
  if ($parent -and (Get-Process -Id $parent -ErrorAction SilentlyContinue).ProcessName -eq 'cmd') { $procs += $parent }
  foreach ($id in $procs) { try { Stop-Process -Id $id -Force -ErrorAction Stop } catch {} }
  Start-Sleep -Seconds 2
  Say '已關閉舊的主機視窗／程式' Yellow
}

# 3) 註冊工作排程：登入時啟動＋每 5 分鐘看門狗（程式內有鎖檔，不會重複開）
$arg = "--headless `"$node`" `"$sup`""
$act = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument $arg -WorkingDirectory $here
$t1  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2  = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $act -Trigger $t1, $t2 -Settings $set -Force | Out-Null
Say "已註冊工作排程「$taskName」" Green
Start-ScheduledTask -TaskName $taskName

# 4) 等服務起來
Say '啟動中，請稍候…' Cyan
$ok8787 = $false; $ok8790 = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 1
  $ok8787 = [bool](Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue)
  $ok8790 = [bool](Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue)
  if ($ok8787 -and $ok8790) { break }
}
Say ("OCR 主機（8787）：" + $(if ($ok8787) { '執行中' } else { '尚未起來，請看 output\host.log' })) $(if ($ok8787) { 'Green' } else { 'Red' })
Say ("AI 橋接（8790）：" + $(if ($ok8790) { '執行中' } else { '尚未起來，請看 output\ai-bridge.log' })) $(if ($ok8790) { 'Green' } else { 'Red' })

$dns = ''
try { $dns = ((& $tsExe status --json | ConvertFrom-Json).Self.DNSName).TrimEnd('.') } catch {}
Say ''
if ($dns) {
  Say "這台主機的 Tailscale 名稱：$dns" Green
  Say "  OCR 主機網址：https://${dns}:8443" Green
  Say "  AI 橋接網址：https://${dns}:8790" Green
  Say '請在 Tailscale 後台（login.tailscale.com/admin/dns）確認已開啟 MagicDNS 與 HTTPS 憑證。' Yellow
} else { Say '還沒取得 Tailscale 名稱：請開啟 Tailscale 並登入後，這個服務會自動完成設定。' Yellow }
Say ''
Say '完成。之後不用再開任何視窗；重開機、登入後會自動在背景執行。' Green
Say '狀態檢查：雙擊「主機背景服務-狀態.bat」；不想用了：雙擊「解除安裝主機背景服務.bat」。'
