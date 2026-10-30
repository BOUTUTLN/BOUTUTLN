# Registers BOUTUTLN tasks in Windows Task Scheduler for the current user.
#  - BOUTUTLN-Daily     : every day at 07:30, runs the daily pipeline
#  - BOUTUTLN-OnLogon   : at log on (e.g. after a reboot), recovers and resumes
# Note: a PC that is fully shut down cannot run tasks. "Wake to run" works only
# from sleep/hibernate, and only when wake timers are enabled in power options.
param(
    [string]$DailyTime = "07:30",
    [string]$KrSymbols = "",
    [string]$UsSymbols = ""
)
$ErrorActionPreference = "Stop"
$ProjectDir = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$script = Join-Path $ProjectDir "scripts\windows\run_daily.ps1"
$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -KrSymbols `"$KrSymbols`" -UsSymbols `"$UsSymbols`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $ProjectDir

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$daily = New-ScheduledTaskTrigger -Daily -At $DailyTime
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

Register-ScheduledTask -TaskName "BOUTUTLN-Daily" -Action $action -Trigger $daily `
    -Settings $settings -Description "BOUTUTLN once-a-day analysis (paper/backtest only)" -Force
Register-ScheduledTask -TaskName "BOUTUTLN-OnLogon" -Action $action -Trigger $logon `
    -Settings $settings -Description "BOUTUTLN recovery after reboot" -Force
Get-ScheduledTask -TaskName "BOUTUTLN-*" | Format-Table TaskName, State
