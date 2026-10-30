param([string]$SourceZip = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $Root 'data\logs'
$stage = Join-Path $Root 'data\updates\latest'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$log = Join-Path $logDir 'team-update.log'
$resultPath = Join-Path $Root 'data\team-update-result.json'
$before = ''
function Record([string]$message) {
  Add-Content -LiteralPath $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $message) -Encoding UTF8
}
function WriteResult([bool]$ok, [bool]$updated, [string]$after, [string]$errorMessage) {
  $value = @{ok=$ok;updated=$updated;before=$before;after=$after;error=$errorMessage;
             checked_at=(Get-Date -Format 'o')}
  [IO.File]::WriteAllText($resultPath, ($value | ConvertTo-Json -Compress),
    (New-Object Text.UTF8Encoding($false)))
}
try {
  $localManifest = Get-Content -LiteralPath (Join-Path $Root 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  $before = [string]$localManifest.version
  if ($SourceZip) {
    $zip = [IO.Path]::GetFullPath($SourceZip)
    if (-not (Test-Path -LiteralPath $zip)) { throw 'Local test update ZIP missing.' }
  } else {
    $stamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $manifestUrl = 'https://raw.githubusercontent.com/987144016/tk-send-plugin/main/extension/manifest.json?t=' + $stamp
    $remote = Invoke-RestMethod -Uri $manifestUrl -TimeoutSec 12
    if ([version]$remote.version -le [version]$localManifest.version) {
      Record ('already current: ' + $localManifest.version)
      WriteResult $true $false $before ''
      exit 0
    }
    $zip = Join-Path (Split-Path -Parent $stage) 'main.zip'
    Invoke-WebRequest -Uri 'https://codeload.github.com/987144016/tk-send-plugin/zip/refs/heads/main' `
      -OutFile $zip -TimeoutSec 45 -UseBasicParsing
  }
  Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
  $source = Get-ChildItem -LiteralPath $stage -Directory | Where-Object {
    Test-Path -LiteralPath (Join-Path $_.FullName 'extension\manifest.json')
  } | Select-Object -First 1
  if (-not $source) { throw 'Downloaded ZIP has no extension manifest.' }
  $newManifest = Get-Content -LiteralPath (Join-Path $source.FullName 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if ([version]$newManifest.version -le [version]$localManifest.version) {
    Record ('already current: ' + $localManifest.version)
    WriteResult $true $false $before ''
    exit 0
  }
  foreach ($dir in @('python','scripts','native-host')) {
    $from = Join-Path $source.FullName $dir
    $to = Join-Path $Root $dir
    if (Test-Path -LiteralPath $from) {
      Get-ChildItem -LiteralPath $from -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $to -Force
      }
    }
  }
  $extFrom = Join-Path $source.FullName 'extension'
  $extTo = Join-Path $Root 'extension'
  Get-ChildItem -LiteralPath $extFrom -File | Where-Object {
    $_.Name -notin @('manifest.json','relay-config.js')
  } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $extTo -Force }
  Copy-Item -LiteralPath (Join-Path $extFrom 'manifest.json') -Destination $extTo -Force
  if (Test-Path -LiteralPath (Join-Path $source.FullName 'VERSION')) {
    Copy-Item -LiteralPath (Join-Path $source.FullName 'VERSION') -Destination $Root -Force
  }
  Record ('updated ' + $localManifest.version + ' -> ' + $newManifest.version + '; Chrome extension reload required')
  WriteResult $true $true ([string]$newManifest.version) ''
} catch {
  Record ('update skipped: ' + $_.Exception.Message)
  WriteResult $false $false $before $_.Exception.Message
  exit 0
}
