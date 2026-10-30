param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$script = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'Build-Team-Installer.ps1'
& $script -OutputDirectory $OutputDirectory
