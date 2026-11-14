# 茲茲主機背景服務：狀態檢查
$here = $PSScriptRoot
$t = Get-ScheduledTask -TaskName '茲茲主機背景服務' -ErrorAction SilentlyContinue
Write-Host ("工作排程：" + $(if ($t) { $t.State } else { '未安裝' }))
$lock = Join-Path $here 'output\host-supervisor.lock'
$pidTxt = if (Test-Path $lock) { (Get-Content $lock).Trim() } else { '' }
$run = $pidTxt -and (Get-Process -Id ([int]$pidTxt) -ErrorAction SilentlyContinue)
Write-Host ("背景程式：" + $(if ($run) { "執行中（pid $pidTxt）" } else { '沒有在跑' }))
foreach ($p in @(@(8787, 'OCR 主機'), @(8790, 'AI 橋接'))) {
  $on = [bool](Get-NetTCPConnection -LocalPort $p[0] -State Listen -ErrorAction SilentlyContinue)
  Write-Host ("{0}（{1}）：{2}" -f $p[1], $p[0], $(if ($on) { '執行中' } else { '沒有在跑' }))
}
$ts = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
try { $dns = ((& $ts status --json | ConvertFrom-Json).Self.DNSName).TrimEnd('.'); Write-Host "Tailscale 名稱：$dns" } catch { Write-Host 'Tailscale：未連線或沒安裝' }
Write-Host "`n最近紀錄（output\host.log）："
if (Test-Path (Join-Path $here 'output\host.log')) { Get-Content (Join-Path $here 'output\host.log') -Tail 12 }
