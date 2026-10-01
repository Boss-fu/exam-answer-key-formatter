# 一次性設定：把「自動更新」註冊成 Windows 工作排程（登入時 + 每 5 分鐘）。
# 在主機上雙擊 setup-autoupdate.bat 執行一次即可；之後推到 GitHub main 的更新，
# 主機會在 5 分鐘內自動拉下來、必要時 npm install、並重開服務。你不必在場。
$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$ps1 = Join-Path $appDir 'auto-update.ps1'
$name = '詳解本機應用 自動更新'

if (-not (Test-Path $ps1)) { Write-Host "找不到 $ps1，請確認在 local-app 資料夾內執行。"; Start-Sleep 6; exit 1 }

$act = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ps1`""
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName $name -Action $act -Trigger $t1, $t2 -Settings $set -Force | Out-Null

Write-Host '===================================='
Write-Host '  已設定自動更新 (工作排程)'
Write-Host '  觸發：登入時 + 每 5 分鐘'
Write-Host '===================================='
Write-Host ''
Write-Host '以後我推到 GitHub main 的更新，主機會在 5 分鐘內自動同步並重開服務，你不必到場。'
Write-Host '紀錄會寫在 local-app\output\auto-update.log。'
Write-Host ''
Write-Host '（前提：這台主機保持開機且維持登入狀態。這個視窗會自動關閉。）'

# 立刻先跑一次，確認能動
Start-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
Start-Sleep 5
