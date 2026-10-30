$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$QueueScript = Join-Path $Root 'python\local_queue.py'
$listeners = Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue
foreach ($pidValue in @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)) {
  if (-not $pidValue) { continue }
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue"
  if (-not $process -or -not $process.CommandLine -or
      $process.CommandLine.IndexOf($QueueScript, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
    throw "Port 8767 is owned by another process ($pidValue); refusing to stop it."
  }
  Stop-Process -Id $pidValue -Force
  Write-Output "Stopped this plugin's queue PID $pidValue"
}
