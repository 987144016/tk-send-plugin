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
$taskLabel = 'reply-team-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
$taskOut = Join-Path $taskOutBase $taskLabel
New-Item -ItemType Directory -Path $taskOut | Out-Null
Copy-Item -LiteralPath (Join-Path $taskRoot 'extension') -Destination (Join-Path $taskOut 'extension') -Recurse
$taskRelay = Join-Path $taskOut 'extension\relay-config.js'
$taskUrlJson = ConvertTo-Json -InputObject ([string]$taskCfg.relay_url) -Compress
$taskKeyJson = ConvertTo-Json -InputObject ([string]$taskCfg.client_key) -Compress
$taskSource = "export const REPLY_RELAY_URL = $taskUrlJson;`nexport const REPLY_RELAY_KEY = $taskKeyJson;`n"
[IO.File]::WriteAllText($taskRelay, $taskSource, (New-Object Text.UTF8Encoding($false)))
$taskZip = $taskOut + '.zip'
Compress-Archive -LiteralPath (Join-Path $taskOut 'extension') -DestinationPath $taskZip
Write-Output ('Private team extension: ' + $taskZip)
Write-Output 'The archive contains a relay client key, not Feishu bot credentials. Share only with authorized teammates.'
