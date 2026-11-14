# 解除安裝茲茲主機背景服務：停掉程式、移除排程，並還原先前被停用的舊「自動更新」排程
$here = $PSScriptRoot
$taskName = '茲茲主機背景服務'
$lock = Join-Path $here 'output\host-supervisor.lock'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
  Write-Host "已移除排程「$taskName」" -ForegroundColor Green
}
if (Test-Path $lock) {
  $id = [int](Get-Content $lock -ErrorAction SilentlyContinue)
  if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { & taskkill /PID $id /T /F | Out-Null; Write-Host '已停止背景程式' -ForegroundColor Green }
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
$rec = Join-Path $here 'output\disabled-old-task.txt'
if (Test-Path $rec) {
  $old = (Get-Content $rec -Raw).Trim()
  if (Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue) { Enable-ScheduledTask -TaskName $old | Out-Null; Write-Host "已還原舊排程「$old」（5 分鐘內會自動把舊服務叫起來）" -ForegroundColor Yellow }
  Remove-Item $rec -Force
}
Write-Host '完成。'
