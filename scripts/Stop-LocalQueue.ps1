# Stop process listening on 8767 (standalone queue only).
$ErrorActionPreference = "Continue"
Get-NetTCPConnection -LocalPort 8767 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { if ($_ -and $_ -ne 0) { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Host "stopped pid $_" } }
Write-Host "done (Chrome left alone)"
