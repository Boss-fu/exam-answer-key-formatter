# 茲茲主機背景服務 安裝（Windows）：一次設定，之後開機登入自動在背景執行（沒有視窗，不會被誤關）
# 包含：OCR 主機＋AI 橋接＋Tailscale HTTPS 入口。重複執行是安全的（會覆蓋舊設定）。
# 若這台電腦原本用「詳解本機應用 自動更新」＋start.bat，會先停用舊的（記錄起來，可用解除安裝腳本還原）。
param([string]$DisplayName = '')
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

# 2.5) 這台主機在網站上的顯示名稱（會自動登錄到匯卷頁的「OCR 運算主機」下拉選單，所有老師都看得到）
$cfgPath = Join-Path $here 'host-config.json'
$cfg = @{}
if (Test-Path $cfgPath) { try { (Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $cfg[$_.Name] = $_.Value } } catch {} }
if (-not $DisplayName -and $cfg.ContainsKey('displayName')) { $DisplayName = [string]$cfg['displayName'] }
if (-not $DisplayName -and -not [Console]::IsInputRedirected) {
  $in = Read-Host '這台主機要在網站上顯示什麼名稱？（例如：劍潭。直接按 Enter 會使用電腦名稱）'
  if ($in) { $DisplayName = $in.Trim() }
}
if (-not $DisplayName) { $DisplayName = $env:COMPUTERNAME }
$cfg['displayName'] = $DisplayName
($cfg | ConvertTo-Json) | Set-Content -LiteralPath $cfgPath -Encoding UTF8
Say "網站上的顯示名稱：$DisplayName" Green

# 3) 註冊工作排程（改用「隱藏啟動器＋系統匣小工具」：沒有任何黑視窗；右下角常駐圖示可叫出狀態）
$vbs = Join-Path $here 'host-tray-launcher.vbs'
if (-not (Test-Path $vbs)) { Say '缺少 host-tray-launcher.vbs，請確認資料夾完整。' Red; exit 1 }
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$act1 = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "//B //Nologo `"$vbs`"" -WorkingDirectory $here
$act2 = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "//B //Nologo `"$vbs`" -Watchdog" -WorkingDirectory $here
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $taskName -Action $act1 -Trigger $t1 -Settings $set -Force | Out-Null
Register-ScheduledTask -TaskName "$taskName-看門狗" -Action $act2 -Trigger $t2 -Settings $set -Force | Out-Null
Say "已註冊工作排程「$taskName」（登入時啟動）與「$taskName-看門狗」（每 5 分鐘確認小工具還在）" Green
# 開始功能表捷徑：小工具被關掉時，可從這裡再叫出來
try {
  $lnk = Join-Path ([Environment]::GetFolderPath('Programs')) '茲茲主機.lnk'
  $w = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
  $w.TargetPath = 'wscript.exe'; $w.Arguments = "//B //Nologo `"$vbs`""; $w.WorkingDirectory = $here; $w.Description = '茲茲主機（OCR 主機＋AI 橋接）'; $w.Save()
  Say '已建立開始功能表捷徑「茲茲主機」' Green
} catch { Say "（捷徑建立失敗，不影響使用：$($_.Exception.Message)）" Yellow }
# 先關掉舊版（conhost 版）的背景程式再啟動新的
$lockFile = Join-Path $out 'host-supervisor.lock'
if (Test-Path $lockFile) { $id = [int](Get-Content $lockFile -ErrorAction SilentlyContinue); if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { & taskkill /PID $id /T /F | Out-Null; Start-Sleep -Seconds 1 } }
Remove-Item (Join-Path $out 'stopped.flag') -Force -ErrorAction SilentlyContinue
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
Say '完成。右下角（可能在「^」隱藏圖示裡）會出現一個紅色「茲」小圖示：點它可看狀態、複製網址、重新啟動。' Green
Say '之後不用再開任何視窗；登入後會自動在背景執行；要真的停止請在圖示右鍵選「結束」。' Green
Say "這台主機會自動登錄到網站（名稱：$DisplayName），匯卷頁的「OCR 運算主機」下拉選單幾分鐘內就會出現，不用手動輸入網址。" Green
Say '狀態檢查：雙擊「主機背景服務-狀態.bat」；不想用了：雙擊「解除安裝主機背景服務.bat」。'
