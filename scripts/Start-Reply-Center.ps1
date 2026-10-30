$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$taskConfig = Join-Path $taskRoot 'config\reply-relay-center.json'
if (-not (Test-Path -LiteralPath $taskConfig)) { throw 'Center relay configuration is missing' }

& (Join-Path $taskRoot 'scripts\Start-LocalQueue-Hidden.ps1')
Write-Output 'Owner queue started with reply center in the same background process.'
