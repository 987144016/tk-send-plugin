$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$taskScript = Join-Path $taskRoot 'scripts\Start-Reply-Center.ps1'
$taskStartup = [Environment]::GetFolderPath('Startup')
$taskShortcut = Join-Path $taskStartup 'TK-SendPlugin-Center.lnk'
$taskShell = New-Object -ComObject WScript.Shell
$taskLink = $taskShell.CreateShortcut($taskShortcut)
$taskLink.TargetPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskLink.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $taskScript + '"'
$taskLink.WindowStyle = 7
$taskLink.WorkingDirectory = $taskRoot
$taskLink.Description = 'Start TK Send Plugin reply center on this Windows user login'
$taskLink.Save()
Write-Output ('Autostart shortcut created only here: ' + $taskShortcut)
