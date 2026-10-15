# Registers (or removes) the two Windows scheduled tasks that collect public Kraken candles.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_candle_schedule.ps1            # install
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_candle_schedule.ps1 -Remove    # uninstall
#
# What the tasks do (public Kraken data only: no key, no orders, no spending):
#   TradeGraph-Candles-Short  every 6 hours: 1-minute candles for BTC/USD and ETH/USD
#   TradeGraph-Candles-Full   daily at 03:30: 5-, 15- and 60-minute candles for every eligible USD pair
# Each task starts the Ubuntu WSL distro if needed and runs scripts/run_candle_collection.sh from a separate,
# pinned checkout (candle-collector-runtime), under one lock so two collections never overlap. Candles go to
# ~/.local/share/trade-graph-market/ inside Ubuntu, reports to its logs/ folder. The tasks run only while you
# are logged in; a run missed while the PC was off starts when it is next available.
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$names = 'TradeGraph-Candles-Short', 'TradeGraph-Candles-Full'

if ($Remove) {
  foreach ($name in $names) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
      Unregister-ScheduledTask -TaskName $name -Confirm:$false
      "removed $name"
    }
  }
  return
}

$runner = '~/.config/superpowers/worktrees/trade-graph/candle-collector-runtime/scripts/run_candle_collection.sh'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew

function New-CandleTask([string]$name, [string]$kind, $trigger, [string]$description) {
  # conhost --headless keeps a console window from flashing on screen at each run.
  $argument = '--headless wsl.exe -d Ubuntu -e bash -lc "bash ' + $runner + ' ' + $kind + '"'
  $action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument $argument
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description $description -Force | Out-Null
  "registered $name"
}

$shortTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddDays(1).AddMinutes(20)) `
  -RepetitionInterval (New-TimeSpan -Hours 6) -RepetitionDuration (New-TimeSpan -Days 3650)
$fullTrigger = New-ScheduledTaskTrigger -Daily -At '03:30'

New-CandleTask $names[0] 'short' $shortTrigger `
  'Trade Graph: public Kraken 1-minute candles for BTC/USD and ETH/USD every 6 hours. Public data only. Disable this task to stop it.'
New-CandleTask $names[1] 'full' $fullTrigger `
  'Trade Graph: public Kraken 5, 15 and 60 minute candles for every eligible USD pair, daily. Public data only. Disable this task to stop it.'

Get-ScheduledTask -TaskName 'TradeGraph-Candles-*' | ForEach-Object {
  $info = Get-ScheduledTaskInfo $_
  [pscustomobject]@{ Task = $_.TaskName; State = $_.State; NextRun = $info.NextRunTime }
} | Format-Table -AutoSize | Out-String -Width 200
