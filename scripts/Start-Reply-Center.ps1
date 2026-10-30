$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$taskConfig = Join-Path $taskRoot 'config\reply-relay-center.json'
if (-not (Test-Path -LiteralPath $taskConfig)) { throw 'Center relay configuration is missing' }

& (Join-Path $taskRoot 'scripts\Start-LocalQueue-Hidden.ps1')
$taskBridge = Join-Path $taskRoot 'python\reply_center_bridge.py'
$taskPython = 'C:\tk邀约\tiktok-outreach\.venv-runtime\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Center Python runtime is missing' }
$taskRunning = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw' -and $_.CommandLine -and
    $_.CommandLine.IndexOf($taskBridge, [StringComparison]::OrdinalIgnoreCase) -ge 0
}
if (-not $taskRunning) {
    Start-Process -FilePath $taskPython -ArgumentList @('"' + $taskBridge + '"') `
        -WorkingDirectory $taskRoot -WindowStyle Hidden
}
Write-Output 'Reply center start requested on this computer only.'
