param(
  [string]$OutputDirectory = '',
  [string]$PythonRuntime = 'C:\Users\123\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none'
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Private = Join-Path $Root 'config\reply-relay-center.json'
$OwnerConfig = Join-Path $Root 'config\one-mailbox.json'
if (-not (Test-Path -LiteralPath $Private) -or -not (Test-Path -LiteralPath $OwnerConfig)) {
  throw 'Owner relay and Feishu table configuration are required to build the private team package.'
}
$relay = Get-Content -LiteralPath $Private -Raw -Encoding UTF8 | ConvertFrom-Json
$owner = Get-Content -LiteralPath $OwnerConfig -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $relay.relay_url -or -not $relay.client_key -or
    -not $owner.feishu.base_token -or -not $owner.feishu.table_id) {
  throw 'Relay client key or shared Feishu table ID is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PythonRuntime 'pythonw.exe'))) {
  throw "Bundled Python runtime not found: $PythonRuntime"
}
$outBase = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $Root 'dist' }
New-Item -ItemType Directory -Path $outBase -Force | Out-Null
$version = (Get-Content -LiteralPath (Join-Path $Root 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
$out = Join-Path $outBase ('TK-Send-Team-v' + $version + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $out | Out-Null
foreach ($name in @('extension','python','native-host','scripts','config','runtime','data\jobs','data\logs')) {
  New-Item -ItemType Directory -Path (Join-Path $out $name) -Force | Out-Null
}
Copy-Item -Path (Join-Path $Root 'extension\*') -Destination (Join-Path $out 'extension') -Recurse
Copy-Item -Path (Join-Path $Root 'python\*.py') -Destination (Join-Path $out 'python')
foreach ($name in @('tk_send_queue_host.py','tk_send_queue_host.bat')) {
  Copy-Item -LiteralPath (Join-Path $Root ('native-host\' + $name)) -Destination (Join-Path $out 'native-host')
}
foreach ($name in @('Bootstrap-Team.ps1','Install-NativeHost.ps1','Start-LocalQueue-Hidden.ps1',
                    'Start-LocalQueue-Silent.vbs','Try-GitPullOnStart.ps1','Update-Team-OnStart.ps1',
                    'Stop-LocalQueue.ps1')) {
  Copy-Item -LiteralPath (Join-Path $Root ('scripts\' + $name)) -Destination (Join-Path $out 'scripts')
}
Copy-Item -LiteralPath (Join-Path $Root '一点即运行-发信插件.cmd') -Destination $out
Copy-Item -LiteralPath (Join-Path $Root 'config\one-mailbox.example.json') -Destination (Join-Path $out 'config')
$teamConfig = Get-Content -LiteralPath (Join-Path $Root 'config\one-mailbox.example.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$teamConfig.armed = $true
$teamConfig.feishu.base_token = [string]$owner.feishu.base_token
$teamConfig.feishu.table_id = ''
$teamConfig.feishu.table_name = ''
$teamConfig.subject = [string]$owner.subject
$utf8 = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $out 'config\one-mailbox.json'),
  ($teamConfig | ConvertTo-Json -Depth 12), $utf8)
$client = @{relay_url=[string]$relay.relay_url;client_key=[string]$relay.client_key}
[IO.File]::WriteAllText((Join-Path $out 'config\team-relay.json'),
  ($client | ConvertTo-Json -Depth 4), $utf8)
$url = ConvertTo-Json -InputObject ([string]$relay.relay_url) -Compress
$key = ConvertTo-Json -InputObject ([string]$relay.client_key) -Compress
[IO.File]::WriteAllText((Join-Path $out 'extension\relay-config.js'),
  "export const REPLY_RELAY_URL = $url;`nexport const REPLY_RELAY_KEY = $key;`n", $utf8)
foreach ($name in @('Lib','DLLs')) {
  Copy-Item -LiteralPath (Join-Path $PythonRuntime $name) -Destination (Join-Path $out 'runtime') -Recurse
}
foreach ($name in @('python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll',
                    'vcruntime140_1.dll','LICENSE.txt')) {
  Copy-Item -LiteralPath (Join-Path $PythonRuntime $name) -Destination (Join-Path $out 'runtime')
}
$wheel = Get-ChildItem -LiteralPath (Join-Path $Root 'vendor') -Filter 'tzdata-*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw 'Vendor tzdata wheel is missing.' }
& (Join-Path $PythonRuntime 'python.exe') -m pip install --no-index --no-deps --target (Join-Path $out 'runtime\Lib\site-packages') $wheel.FullName | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not include timezone data.' }
$guide = @'
TK Send Plugin 团队版安装（Windows + Chrome）

1. 解压整个 ZIP，保留文件夹结构。不要只拿 extension 文件夹。
2. Chrome 打开 chrome://extensions，开启开发者模式，加载已解压的扩展程序：选择本包的 extension 子文件夹（里面有 manifest.json）。
3. 双击包根目录的「一点即运行-发信插件.cmd」。成功时无黑色常驻窗口；失败会弹出具体原因，日志在 data\logs\setup-result.txt。
4. 点击插件图标打开选项，填自己的 Gmail、自己的 Gmail 模板名称、自己数据表的 table_id（tbl 开头）。保存参数，点「连接并启动」。不要填他人的 Gmail 或表。

包内自带 Python、时区数据、本地队列、Native Host。电脑不需要安装 Python、飞书 CLI 或影刀。群播报和飞书表读写目前经管理员电脑中转；管理员电脑离线时这些操作会等恢复。请勿把此私有包公开上传。
'@
[IO.File]::WriteAllText((Join-Path $out '安装说明.txt'), $guide, $utf8)
$zip = $out + '.zip'
Compress-Archive -Path (Join-Path $out '*') -DestinationPath $zip
Write-Output ('Complete private team installer: ' + $zip)
