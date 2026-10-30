param([string]$ExtensionId = '')
# Install Chrome Native Messaging host for this extracted bundle's extension.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$HostDir = Join-Path $Root "native-host"
$Bat = Join-Path $HostDir "tk_send_queue_host.bat"
$HostBin = $Bat
$ManifestPath = Join-Path $HostDir "com.tk.outreach.sendqueue.json"
$HostName = "com.tk.outreach.sendqueue"

# Discover unpacked extension id(s) that load this package's extension\
$ExtPathNeedle = [IO.Path]::GetFullPath((Join-Path $Root "extension")).TrimEnd('\','/').ToLowerInvariant().Replace('\','/')
$ids = New-Object System.Collections.Generic.List[string]
if ($ExtensionId -match '^[a-p]{32}$') { $ids.Add($ExtensionId) | Out-Null }

$chromeBase = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
if (Test-Path $chromeBase) {
  Get-ChildItem $chromeBase -Directory | ForEach-Object {
    foreach ($pref in @('Secure Preferences', 'Preferences')) {
      $sp = Join-Path $_.FullName $pref
      if (-not (Test-Path $sp)) { continue }
      try {
        $d = Get-Content -LiteralPath $sp -Raw -Encoding UTF8 | ConvertFrom-Json
        $settings = $d.extensions.settings
        if (-not $settings) { continue }
        $settings.PSObject.Properties | ForEach-Object {
          $eid = $_.Name
          $path = [string]$_.Value.path
          if (-not $path -or $eid -notmatch '^[a-p]{32}$') { return }
          try { $norm = [IO.Path]::GetFullPath($path).TrimEnd('\','/').ToLowerInvariant().Replace('\','/') }
          catch { return }
          if ($norm -eq $ExtPathNeedle -and -not $ids.Contains($eid)) { $ids.Add($eid) | Out-Null }
        }
      } catch {}
    }
  }
}

$origins = @()
foreach ($id in $ids) {
  if ($id -and $id.Length -ge 32) { $origins += ("chrome-extension://" + $id + "/") }
}
if ($origins.Count -eq 0) {
  throw "Chrome has not loaded this bundle's extension folder yet: $ExtPathNeedle. In chrome://extensions select the bundle's extension folder, then run the launcher again."
}

$batPath = (Resolve-Path -LiteralPath $HostBin).Path
# Chrome on Windows wants escaped backslashes in JSON path
$batJson = $batPath -replace '\\','\\'
$manifest = [ordered]@{
  name = $HostName
  description = "TK Send Plugin local queue starter (8767)"
  path = $batPath
  type = "stdio"
  allowed_origins = $origins
}
$json = ($manifest | ConvertTo-Json -Depth 5)
# ConvertTo-Json may not escape path the way Chrome wants for path field - write manually
$originsJson = ($origins | ForEach-Object { '"' + $_ + '"' }) -join ", "
$manual = @"
{
  "name": "$HostName",
  "description": "TK Send Plugin local queue starter (8767)",
  "path": "$($batPath.Replace('\','\\'))",
  "type": "stdio",
  "allowed_origins": [ $originsJson ]
}
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($ManifestPath, $manual, $utf8NoBom)
$regPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$HostName"
New-Item -Path $regPath -Force | Out-Null
Set-ItemProperty -Path $regPath -Name "(default)" -Value $ManifestPath

# Also register custom protocol fallback tk-send-queue://ensure
$proto = "HKCU:\Software\Classes\tk-send-queue"
New-Item -Path $proto -Force | Out-Null
Set-ItemProperty -Path $proto -Name "(default)" -Value "URL:TK Send Queue Protocol"
Set-ItemProperty -Path $proto -Name "URL Protocol" -Value ""
New-Item -Path "$proto\shell\open\command" -Force | Out-Null
$cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\scripts\Start-LocalQueue-Hidden.ps1`""
Set-ItemProperty -Path "$proto\shell\open\command" -Name "(default)" -Value $cmd

Write-Host "Native host installed: $HostName"
Write-Host "Manifest: $ManifestPath"
Write-Host "allowed_origins: $($origins -join ', ')"
Write-Host "Protocol: tk-send-queue://ensure"
Write-Host "OK"
