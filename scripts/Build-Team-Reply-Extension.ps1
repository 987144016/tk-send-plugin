param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$taskPrivate = Join-Path $taskRoot 'config\reply-relay-center.json'
if (-not (Test-Path -LiteralPath $taskPrivate)) { throw 'Center relay configuration is missing' }
$taskCfg = Get-Content -LiteralPath $taskPrivate -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$taskCfg.relay_url -notmatch '^https://[A-Za-z0-9.-]+$' -or
    [string]$taskCfg.client_key -notmatch '^[A-Za-z0-9_-]{30,}$') {
    throw 'Center relay URL/client key is invalid'
}
$taskOutBase = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else {
    Join-Path $taskRoot 'dist'
}
New-Item -ItemType Directory -Path $taskOutBase -Force | Out-Null
$taskVersion = (Get-Content -LiteralPath (Join-Path $taskRoot 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
$taskLabel = 'TK-Send-Plugin-v' + $taskVersion + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$taskOut = Join-Path $taskOutBase $taskLabel
New-Item -ItemType Directory -Path $taskOut | Out-Null
# The extracted folder itself is the Chrome extension root. Do not include
# Python/backend files or an extra "extension" directory in the archive.
Copy-Item -Path (Join-Path $taskRoot 'extension\*') -Destination $taskOut -Recurse
$taskRelay = Join-Path $taskOut 'relay-config.js'
$taskUrlJson = ConvertTo-Json -InputObject ([string]$taskCfg.relay_url) -Compress
$taskKeyJson = ConvertTo-Json -InputObject ([string]$taskCfg.client_key) -Compress
$taskSource = "export const REPLY_RELAY_URL = $taskUrlJson;`nexport const REPLY_RELAY_KEY = $taskKeyJson;`n"
[IO.File]::WriteAllText($taskRelay, $taskSource, (New-Object Text.UTF8Encoding($false)))
$taskZip = $taskOut + '.zip'
Compress-Archive -Path (Join-Path $taskOut '*') -DestinationPath $taskZip
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskArchive = [IO.Compression.ZipFile]::OpenRead($taskZip)
try {
    $taskEntries = @($taskArchive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    if ($taskEntries -notcontains 'manifest.json') { throw 'Package root has no manifest.json' }
    if (@($taskEntries | Where-Object { $_ -match '(^|/)_'}).Count) {
        throw 'Package contains a Chrome-reserved underscore filename'
    }
} finally { $taskArchive.Dispose() }
Write-Output ('Private team extension: ' + $taskZip)
Write-Output 'Unzip and select the extracted folder containing manifest.json in Chrome.'
Write-Output 'The archive contains a relay client key, not Feishu bot credentials. Share only with authorized teammates.'
