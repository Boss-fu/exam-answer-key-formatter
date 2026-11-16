' 茲茲主機：隱藏啟動系統匣小工具（沒有任何視窗）。給工作排程器與開始功能表捷徑使用。
' 用法：wscript.exe //B host-tray-launcher.vbs [-Watchdog]
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
extra = ""
If WScript.Arguments.Count > 0 Then extra = " " & WScript.Arguments(0)
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & here & "\host-tray.ps1""" & extra, 0, False
