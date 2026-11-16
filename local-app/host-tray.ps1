# 茲茲主機：系統匣小工具（Windows）。右下角常駐一個小圖示，像 Tailscale 一樣：
#   - 平常在背景跑（沒有黑視窗、不會被誤關）；點圖示可叫出狀態視窗
#   - 負責啟動並看守「OCR 主機＋AI 橋接」（host-supervisor.js）；掛掉會自動重開
#   - 右鍵選單：開啟狀態、複製網址、重新啟動、開啟紀錄、結束（要確認才會停止）
# 由工作排程器經 host-tray-launcher.vbs 隱藏啟動；不要直接雙擊本檔（會多一個 PowerShell 視窗）。
param([switch]$Watchdog, [switch]$SelfTest)
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$out = Join-Path $here 'output'
New-Item -ItemType Directory -Force $out | Out-Null
$logFile = Join-Path $out 'tray.log'
$flag = Join-Path $out 'stopped.flag'
function Log($m) { try { Add-Content -LiteralPath $logFile -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 } catch {} }

# 使用者從選單按過「結束」之後，看門狗不要自己又叫回來；登入啟動／手動開啟會清掉旗標
if ($Watchdog -and (Test-Path $flag)) { exit 0 }
Remove-Item $flag -Force -ErrorAction SilentlyContinue

# 只允許一份（同一個登入工作階段）
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\ZiziHostTray', [ref]$created)
if (-not $created -and -not $SelfTest) { exit 0 }

$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { $p = Join-Path $env:ProgramFiles 'nodejs\node.exe'; if (Test-Path $p) { $node = $p } }
$sup = Join-Path $here 'host-supervisor.js'
$tsExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
$lock = Join-Path $out 'host-supervisor.lock'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if ($SelfTest) {
  Write-Output ("node: " + $node)
  Write-Output ("supervisor: " + (Test-Path $sup))
  Write-Output ("tailscale: " + (Test-Path $tsExe))
  Write-Output ("forms: OK")
  exit 0
}
Log "=== 小工具啟動（watchdog=$Watchdog）==="

# ---------- 工具函式 ----------
function Test-Port($port) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(300)
    $res = ($ok -and $c.Connected)
    $c.Close(); return $res
  } catch { return $false }
}
function Get-SupPid {
  try {
    if (-not (Test-Path $lock)) { return 0 }
    $id = [int](Get-Content $lock -ErrorAction Stop | Select-Object -First 1)
    if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { return $id }
  } catch {}
  return 0
}
function Start-Supervisor {
  if (-not $node) { Log '找不到 node.exe，無法啟動主機'; return }
  if (Get-SupPid) { return }
  try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $node; $psi.Arguments = '"' + $sup + '"'; $psi.WorkingDirectory = $here
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    [void][System.Diagnostics.Process]::Start($psi)
    Log '已啟動主機服務'
  } catch { Log ("啟動主機服務失敗：" + $_.Exception.Message) }
}
function Stop-Supervisor {
  $id = Get-SupPid
  if ($id) { try { & taskkill.exe /PID $id /T /F 2>&1 | Out-Null; Log "已停止主機服務 pid=$id" } catch { Log ("停止失敗：" + $_.Exception.Message) } }
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
$script:dns = ''; $script:dnsAt = [DateTime]::MinValue
function Get-Dns {
  if (((Get-Date) - $script:dnsAt).TotalSeconds -lt 60 -and $script:dns) { return $script:dns }
  $script:dnsAt = Get-Date
  try { if (Test-Path $tsExe) { $script:dns = ((& $tsExe status --json | ConvertFrom-Json).Self.DNSName).TrimEnd('.') } } catch { $script:dns = '' }
  return $script:dns
}
function Get-State {
  $ocr = Test-Port 8787; $ai = Test-Port 8790
  [pscustomobject]@{ Ocr = $ocr; Ai = $ai; Ok = ($ocr -and $ai); Dns = (Get-Dns) }
}
function Get-Urls($st) {
  if ($st.Dns) { [pscustomobject]@{ Ocr = "https://$($st.Dns):8443"; Ai = "https://$($st.Dns):8790" } }
  else { [pscustomobject]@{ Ocr = ''; Ai = '' } }
}
function Tail-Log($n) { $f = Join-Path $out 'host.log'; if (Test-Path $f) { (Get-Content $f -Tail $n -Encoding UTF8) -join "`r`n" } else { '（還沒有紀錄）' } }

# ---------- 圖示 ----------
function New-StatusIcon($ok) {
  $bmp = New-Object System.Drawing.Bitmap 32, 32
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'
  $bg = if ($ok) { [System.Drawing.Color]::FromArgb(133, 1, 3) } else { [System.Drawing.Color]::FromArgb(110, 111, 116) }
  $g.FillEllipse((New-Object System.Drawing.SolidBrush $bg), 1, 1, 30, 30)
  $font = New-Object System.Drawing.Font('Microsoft JhengHei', 15, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
  $sf = New-Object System.Drawing.StringFormat; $sf.Alignment = 'Center'; $sf.LineAlignment = 'Center'
  $g.DrawString([string][char]0x8332, $font, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF 0, 1, 32, 32), $sf)
  $dot = if ($ok) { [System.Drawing.Color]::FromArgb(46, 204, 113) } else { [System.Drawing.Color]::FromArgb(231, 76, 60) }
  $g.FillEllipse([System.Drawing.Brushes]::White, 19, 19, 12, 12)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush $dot), 21, 21, 8, 8)
  $g.Dispose()
  return [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
}

[System.Windows.Forms.Application]::EnableVisualStyles()
$iconOk = New-StatusIcon $true
$iconBad = New-StatusIcon $false
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $iconBad
$notify.Text = '茲茲主機：啟動中…'
$notify.Visible = $true

# ---------- 狀態視窗 ----------
$script:form = $null
function Show-StatusForm {
  if ($script:form -and -not $script:form.IsDisposed) { $script:form.Show(); $script:form.Activate(); Update-Form; return }
  $f = New-Object System.Windows.Forms.Form
  $f.Text = '茲茲主機'; $f.StartPosition = 'CenterScreen'; $f.Size = New-Object System.Drawing.Size(640, 520)
  $f.Font = New-Object System.Drawing.Font('Microsoft JhengHei UI', 10)
  $f.ShowInTaskbar = $true; $f.MaximizeBox = $false; $f.Icon = $iconOk
  $lbl = New-Object System.Windows.Forms.Label; $lbl.Name = 'lbl'; $lbl.Location = '14,12'; $lbl.Size = '600,150'; $lbl.Font = New-Object System.Drawing.Font('Microsoft JhengHei UI', 11)
  $tb = New-Object System.Windows.Forms.TextBox; $tb.Name = 'tb'; $tb.Multiline = $true; $tb.ReadOnly = $true; $tb.ScrollBars = 'Vertical'; $tb.Location = '14,170'; $tb.Size = '600,250'; $tb.Font = New-Object System.Drawing.Font('Consolas', 9)
  function Add-Btn($text, $x, $w, $handler) { $b = New-Object System.Windows.Forms.Button; $b.Text = $text; $b.Location = New-Object System.Drawing.Point($x, 432); $b.Size = New-Object System.Drawing.Size($w, 34); $b.Add_Click($handler); $f.Controls.Add($b) }
  Add-Btn '複製 OCR 網址' 14 130 { $u = Get-Urls (Get-State); if ($u.Ocr) { [System.Windows.Forms.Clipboard]::SetText($u.Ocr) } }
  Add-Btn '複製 AI 橋接網址' 154 140 { $u = Get-Urls (Get-State); if ($u.Ai) { [System.Windows.Forms.Clipboard]::SetText($u.Ai) } }
  Add-Btn '重新啟動服務' 304 130 { Restart-Host }
  Add-Btn '關閉視窗' 504 110 { $script:form.Hide() }
  $f.Controls.Add($lbl); $f.Controls.Add($tb)
  $f.Add_FormClosing({ param($s, $e) if ($e.CloseReason -eq 'UserClosing') { $e.Cancel = $true; $s.Hide() } })
  $script:form = $f
  Update-Form
  $f.Show(); $f.Activate()
}
function Update-Form {
  if (-not $script:form -or $script:form.IsDisposed -or -not $script:form.Visible) { return }
  $st = Get-State; $u = Get-Urls $st
  $t = @()
  $t += ("OCR 主機（8787）：" + $(if ($st.Ocr) { '執行中' } else { '沒有在跑' }))
  $t += ("AI 橋接（8790）：" + $(if ($st.Ai) { '執行中' } else { '沒有在跑' }))
  $t += ("Tailscale 名稱：" + $(if ($st.Dns) { $st.Dns } else { '尚未連線' }))
  $t += ''
  $t += ("OCR 主機網址：" + $(if ($u.Ocr) { $u.Ocr } else { '（Tailscale 連線後顯示）' }))
  $t += ("AI 橋接網址：" + $(if ($u.Ai) { $u.Ai } else { '（Tailscale 連線後顯示）' }))
  try {
    $rf = Join-Path $out 'registry-state.json'
    if (Test-Path $rf) {
      $r = Get-Content $rf -Raw -Encoding UTF8 | ConvertFrom-Json
      $mins = [int]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $r.at) / 60000
      if ($r.ok) { $t += ("網站登錄：已登錄為「" + $r.name + "」（" + [int]$mins + " 分鐘前更新）") } else { $t += ("網站登錄：尚未完成（" + $r.error + "）") }
    } else { $t += '網站登錄：等待第一次登錄（開機後約 15 秒）' }
  } catch {}
  $script:form.Controls['lbl'].Text = ($t -join "`r`n")
  $script:form.Controls['tb'].Text = Tail-Log 40
  $script:form.Controls['tb'].SelectionStart = $script:form.Controls['tb'].Text.Length; $script:form.Controls['tb'].ScrollToCaret()
}
function Restart-Host {
  Log '使用者要求重新啟動服務'
  Stop-Supervisor; Start-Sleep -Seconds 2; Start-Supervisor
  $notify.ShowBalloonTip(3000, '茲茲主機', '已重新啟動，請稍候幾秒', [System.Windows.Forms.ToolTipIcon]::Info)
}

# ---------- 右鍵選單 ----------
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miStatus = $menu.Items.Add('狀態：檢查中…'); $miStatus.Enabled = $false
[void]$menu.Items.Add('-')
$miOpen = $menu.Items.Add('開啟狀態視窗'); $miOpen.Font = New-Object System.Drawing.Font($miOpen.Font, [System.Drawing.FontStyle]::Bold)
$miCopyOcr = $menu.Items.Add('複製 OCR 主機網址')
$miCopyAi = $menu.Items.Add('複製 AI 橋接網址')
[void]$menu.Items.Add('-')
$miRestart = $menu.Items.Add('重新啟動服務')
$miLog = $menu.Items.Add('開啟紀錄資料夾')
[void]$menu.Items.Add('-')
$miExit = $menu.Items.Add('結束（停止主機）')
$notify.ContextMenuStrip = $menu
$miOpen.Add_Click({ Show-StatusForm })
$notify.Add_DoubleClick({ Show-StatusForm })
$miCopyOcr.Add_Click({ $u = Get-Urls (Get-State); if ($u.Ocr) { [System.Windows.Forms.Clipboard]::SetText($u.Ocr); $notify.ShowBalloonTip(2000, '茲茲主機', '已複製 OCR 主機網址', 'Info') } else { $notify.ShowBalloonTip(3000, '茲茲主機', '還沒取得網址：請確認 Tailscale 已連線', 'Warning') } })
$miCopyAi.Add_Click({ $u = Get-Urls (Get-State); if ($u.Ai) { [System.Windows.Forms.Clipboard]::SetText($u.Ai); $notify.ShowBalloonTip(2000, '茲茲主機', '已複製 AI 橋接網址', 'Info') } else { $notify.ShowBalloonTip(3000, '茲茲主機', '還沒取得網址：請確認 Tailscale 已連線', 'Warning') } })
$miRestart.Add_Click({ Restart-Host })
$miLog.Add_Click({ Start-Process explorer.exe $out })
$miExit.Add_Click({
  $r = [System.Windows.Forms.MessageBox]::Show("結束後，老師就連不到這台主機（OCR 與 AI 分類都會停止）。`n確定要結束嗎？", '茲茲主機', 'YesNo', 'Warning', 'Button2')
  if ($r -eq 'Yes') {
    Log '使用者從選單結束'
    Set-Content -LiteralPath $flag -Value (Get-Date -Format 'o')
    Stop-Supervisor
    $notify.Visible = $false; $notify.Dispose()
    [System.Windows.Forms.Application]::Exit()
  }
})

# ---------- 定時檢查（每 5 秒）：更新圖示、掛掉自動重開 ----------
$script:down = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({
  try {
    $st = Get-State
    $notify.Icon = $(if ($st.Ok) { $iconOk } else { $iconBad })
    $txt = if ($st.Ok) { '運作中' } elseif ($st.Ocr -or $st.Ai) { '部分服務未啟動' } else { '服務沒有在跑' }
    $miStatus.Text = "狀態：$txt"
    $notify.Text = "茲茲主機：$txt"
    if ($st.Ok) { $script:down = 0 } else { $script:down++; if ($script:down -ge 3) { Log '偵測到服務沒有在跑，嘗試重開'; Start-Supervisor; $script:down = 0 } }
    Update-Form
  } catch { Log ("定時檢查錯誤：" + $_.Exception.Message) }
})
$timer.Start()

Start-Supervisor
$notify.ShowBalloonTip(3000, '茲茲主機', '已在背景執行。點右下角圖示可查看狀態。', [System.Windows.Forms.ToolTipIcon]::Info)
try { [System.Windows.Forms.Application]::Run() } catch { Log ("小工具錯誤：" + $_.Exception.Message) }
try { $notify.Visible = $false; $notify.Dispose(); $mutex.ReleaseMutex() } catch {}
Log '=== 小工具結束 ==='
