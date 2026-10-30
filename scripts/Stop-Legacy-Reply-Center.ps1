$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Bridge = Join-Path $Root 'python\reply_center_bridge.py'
$running = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^pythonw' -and $_.CommandLine -and
  $_.CommandLine.IndexOf($Bridge, [StringComparison]::OrdinalIgnoreCase) -ge 0
}
foreach ($process in $running) {
  Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
}
if ($running) {
  Start-Sleep -Milliseconds 500
  $remaining = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw' -and $_.CommandLine -and
    $_.CommandLine.IndexOf($Bridge, [StringComparison]::OrdinalIgnoreCase) -ge 0
  }
  if ($remaining) { throw 'Old reply center is still running.' }
}
