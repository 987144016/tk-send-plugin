# Update-SendPlugin.ps1 — manual fallback updater for TK Send Plugin
# Prefer: starting the local queue auto-runs git pull --ff-only (see scripts/Try-GitPullOnStart.ps1).
# Use this script as a manual fallback, then reload the extension.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

Write-Host "=== TK Send Plugin manual update ===" -ForegroundColor Cyan
Write-Host "Root: $Root"

$verFile = Join-Path $Root "VERSION"
$before = if (Test-Path -LiteralPath $verFile) { (Get-Content -LiteralPath $verFile -Raw).Trim() } else { "(none)" }
Write-Host "Local VERSION before: $before"

if (-not (Test-Path -LiteralPath (Join-Path $Root ".git"))) {
  Write-Host ""
  Write-Host "This folder is not a git clone." -ForegroundColor Yellow
  Write-Host "First-time install (recommended):"
  Write-Host "  git clone https://github.com/987144016/tk-send-plugin.git `"$Root`""
  Write-Host "Or clone elsewhere and load that clone's extension\ in chrome://extensions."
  Write-Host "Then re-run this script, or start the queue once (auto pull)."
  exit 2
}

Write-Host "Running: git pull --ff-only"
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
  Write-Host "git pull failed (exit $LASTEXITCODE). Fix conflicts / network, then retry." -ForegroundColor Red
  exit $LASTEXITCODE
}

$after = if (Test-Path -LiteralPath $verFile) { (Get-Content -LiteralPath $verFile -Raw).Trim() } else { "(none)" }
Write-Host "Local VERSION after:  $after" -ForegroundColor Green
Write-Host ""
Write-Host "Next: open chrome://extensions and click Reload on TK Send Plugin." -ForegroundColor Yellow
Write-Host "(Unpacked extensions do not hot-swap; reload is required.)"