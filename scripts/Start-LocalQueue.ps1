# Start standalone send-plugin local queue (port 8767). Does not touch old workbench 8765.
# Click-to-run twin: ..\一点即运行-发信插件.cmd
# Params (mailbox / template / Feishu / pace) come from config\one-mailbox.json — editable in Options.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
# Auto-update (simple): git pull --ff-only when this is a clone; never blocks start
& "$Root\scripts\Try-GitPullOnStart.ps1" | Out-Host
Write-Host "send-plugin root: $Root"
$CfgPath = Join-Path $Root "config\one-mailbox.json"
if (Test-Path -LiteralPath $CfgPath) {
  try {
    $c = Get-Content -LiteralPath $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $mb = $c.active_mailbox; if (-not $mb) { $mb = $c.mailbox }
    $tpl = $c.template_name
    $ft = $null; if ($c.feishu) { $ft = $c.feishu.table_name }
    $wm = 30; $bs = 15
    if ($c.pace) { if ($c.pace.window_minutes) { $wm = $c.pace.window_minutes }; if ($c.pace.max_messages_per_window) { $bs = $c.pace.max_messages_per_window } }
    Write-Host "config: mailbox=$mb / template=$tpl / feishu=$ft / pace=${wm}min/${bs}人"
  } catch {
    Write-Host "config: (could not parse one-mailbox.json)"
  }
} else {
  Write-Host "config missing: $CfgPath"
}
Write-Host "rules: no workbench / no pair codes / Feishu-only / NO mass-send beyond pace / Gmail web click only"
python "$Root\python\local_queue.py" --config "$Root\config\one-mailbox.json"
