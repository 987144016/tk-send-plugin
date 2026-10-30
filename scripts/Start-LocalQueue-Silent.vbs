Option Explicit
Dim fs, sh, root, ps, install, start
Set fs = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fs.GetParentFolderName(fs.GetParentFolderName(WScript.ScriptFullName))
ps = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "
install = fs.BuildPath(root, "scripts\Install-NativeHost.ps1")
start = fs.BuildPath(root, "scripts\Start-LocalQueue-Hidden.ps1")
sh.Run ps & Chr(34) & install & Chr(34), 0, True
sh.Run ps & Chr(34) & start & Chr(34), 0, False
