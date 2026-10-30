# Entry point used by Windows Task Scheduler. Recovers interrupted runs, then runs the
# once-a-day pipeline. Safe to run repeatedly: a completed day is never run twice.
param(
    [string]$KrSymbols = "",   # e.g. "005930,000660" - symbols that must be fresh
    [string]$UsSymbols = ""    # e.g. "SPY,QQQ"
)
$ErrorActionPreference = "Stop"
$ProjectDir = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $ProjectDir
$exe = Join-Path $ProjectDir ".venv\Scripts\boututln.exe"
$logDir = Join-Path $ProjectDir "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily-" + (Get-Date -Format "yyyyMMdd") + ".log")
& $exe recover *>> $log
& $exe daily-run --kr $KrSymbols --us $UsSymbols *>> $log
exit $LASTEXITCODE
