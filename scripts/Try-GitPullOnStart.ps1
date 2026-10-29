# Try-GitPullOnStart.ps1 — best-effort git pull when local queue starts.
# Never blocks queue start. Only runs if this install is a git clone.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $Root

if (-not (Test-Path -LiteralPath (Join-Path $Root ".git"))) {
  Write-Host "[update] skip: not a git clone ($Root)"
  exit 0
}

$verFile = Join-Path $Root "VERSION"
$before = if (Test-Path -LiteralPath $verFile) { (Get-Content -LiteralPath $verFile -Raw).Trim() } else { "(none)" }
Write-Host "[update] git pull --ff-only (before VERSION=$before)"
git pull --ff-only 2>&1 | ForEach-Object { Write-Host $_ }
$code = $LASTEXITCODE
$after = if (Test-Path -LiteralPath $verFile) { (Get-Content -LiteralPath $verFile -Raw).Trim() } else { "(none)" }
if ($code -ne 0) {
  Write-Host "[update] git pull failed (exit $code); continuing to start queue. Manual: Update-SendPlugin.ps1" -ForegroundColor Yellow
  exit 0
}
if ($before -ne $after) {
  Write-Host "[update] updated $before -> $after" -ForegroundColor Green
  Write-Host "[update] IMPORTANT: open chrome://extensions and Reload this extension, or new JS will not apply." -ForegroundColor Yellow
} else {
  Write-Host "[update] already up to date (VERSION=$after)"
}
exit 0
