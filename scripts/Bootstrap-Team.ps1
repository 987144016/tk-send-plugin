$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $Root 'data\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$resultPath = Join-Path $logDir 'setup-result.txt'
try {
  if (-not (Test-Path -LiteralPath (Join-Path $Root 'extension\manifest.json'))) {
    throw 'Complete team package is missing extension\manifest.json.'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $Root 'python\local_queue.py'))) {
    throw 'Complete team package is missing python\local_queue.py.'
  }
  $config = Join-Path $Root 'config\one-mailbox.json'
  if (-not (Test-Path -LiteralPath $config)) {
    Copy-Item -LiteralPath (Join-Path $Root 'config\one-mailbox.example.json') -Destination $config
  }
  $hostError = ''
  try {
    & (Join-Path $Root 'scripts\Install-NativeHost.ps1') | Out-Null
  } catch {
    $hostError = $_.Exception.Message
  }
  & (Join-Path $Root 'scripts\Start-LocalQueue-Hidden.ps1') | Out-Null
  if ($hostError) {
    $message = '本地队列已启动，但 Native Host 未注册：' + $hostError +
      "`r`n请确认 Chrome 加载的是此目录：" + (Join-Path $Root 'extension')
    [IO.File]::WriteAllText($resultPath, $message, [Text.Encoding]::Unicode)
    exit 2
  }
  [IO.File]::WriteAllText($resultPath, 'OK: Native Host installed and local queue online.', [Text.Encoding]::Unicode)
  exit 0
} catch {
  $message = '启动失败：' + $_.Exception.Message + "`r`n安装目录：" + $Root
  [IO.File]::WriteAllText($resultPath, $message, [Text.Encoding]::Unicode)
  exit 1
}
