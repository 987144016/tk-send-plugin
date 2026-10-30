Option Explicit
Dim fs, sh, root, script
Set fs = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fs.GetParentFolderName(WScript.ScriptFullName)
script = fs.BuildPath(root, "scripts\Start-LocalQueue-Silent.vbs")
sh.Run "wscript.exe //B //Nologo " & Chr(34) & script & Chr(34), 0, False
