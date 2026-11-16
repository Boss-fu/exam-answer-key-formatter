# 解除安裝茲茲主機背景服務：停掉程式、移除排程，並還原先前被停用的舊「自動更新」排程
$here = $PSScriptRoot
$taskName = '茲茲主機背景服務'
$lock = Join-Path $here 'output\host-supervisor.lock'
foreach ($n in @($taskName, "$taskName-看門狗")) {
  if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $n -Confirm:$false
    Write-Host "已移除排程「$n」" -ForegroundColor Green
  }
}
# 關掉系統匣小工具、移除捷徑
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'host-tray\.ps1' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) '茲茲主機.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $here 'output\stopped.flag') -Force -ErrorAction SilentlyContinue
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
