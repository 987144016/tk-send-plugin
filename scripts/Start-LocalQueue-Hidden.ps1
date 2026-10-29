# Silent background start. No console window.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$health = "http://127.0.0.1:8767/api/health"
try {
  $r = Invoke-WebRequest -Uri $health -TimeoutSec 2 -UseBasicParsing
  if ($r.StatusCode -eq 200) { exit 0 }
} catch {}

function Find-Pythonw {
  $cands = @(
    "$env:LOCALAPPDATA\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\pythonw.exe",
    "$env:LOCALAPPDATA\Microsoft\WindowsApps\pythonw.exe"
  )
  foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
  $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "pythonw/python not found"
}

$python = Find-Pythonw
$argList = @("$Root\python\local_queue.py", "--config", "$Root\config\one-mailbox.json")
$stdout = Join-Path $Root "data\logs\queue-stdout.log"
$stderr = Join-Path $Root "data\logs\queue-stderr.log"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data\logs") | Out-Null
Start-Process -FilePath $python -ArgumentList $argList -WorkingDirectory $Root -WindowStyle Hidden `
  -RedirectStandardOutput $stdout -RedirectStandardError $stderr
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 250
  try {
    $r = Invoke-WebRequest -Uri $health -TimeoutSec 2 -UseBasicParsing
    if ($r.StatusCode -eq 200) { exit 0 }
  } catch {}
}
exit 0
