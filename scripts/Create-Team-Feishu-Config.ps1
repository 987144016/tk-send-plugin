param([string]$AppProfile = 'tk-outreach-local', [string]$OutputPath = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LarkConfig = Join-Path $env:USERPROFILE '.lark-cli\config.json'
$App = (Get-Content -LiteralPath $LarkConfig -Raw -Encoding UTF8 | ConvertFrom-Json).apps |
  Where-Object { $_.name -eq $AppProfile } | Select-Object -First 1
if (-not $App -or -not $App.appId) { throw 'Feishu app profile was not found.' }
$Account = 'appsecret:' + [string]$App.appId
$Name = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Account)).TrimEnd('=').Replace('+','-').Replace('/','_')
$Encrypted = (Get-Item 'HKCU:\Software\LarkCli\keychain\lark-cli').GetValue($Name)
if (-not $Encrypted) { throw 'Feishu app secret was not found in the current Windows user keychain.' }
Add-Type -AssemblyName System.Security
$Entropy = [Text.Encoding]::UTF8.GetBytes('lark-cli' + [char]0 + $Account)
$Plain = [Security.Cryptography.ProtectedData]::Unprotect(
  [Convert]::FromBase64String($Encrypted), $Entropy,
  [Security.Cryptography.DataProtectionScope]::CurrentUser)
$Secret = [Text.Encoding]::UTF8.GetString($Plain)
$Local = Get-Content -LiteralPath (Join-Path $Root 'config\one-mailbox.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$Base = [string]$Local.feishu.base_token
$Chat = [string]$Local.reply_notify.chat_id
if (-not $Base) { throw 'The local Feishu base_token is missing.' }
$Value = [ordered]@{
  app_id = [string]$App.appId
  app_secret = $Secret
  base_token = $Base
  chat_id = $Chat
  notify_enabled = [bool]$Chat
  notify_start_after = [DateTimeOffset]::UtcNow.ToString('o')
}
$Target = if ($OutputPath) { [IO.Path]::GetFullPath($OutputPath) } else { Join-Path $Root 'extension\team-feishu.json' }
[IO.File]::WriteAllText($Target, ($Value | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
Write-Output ('Private team file ready: ' + $Target)
