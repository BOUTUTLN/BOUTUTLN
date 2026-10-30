# Sets Ollama to local-only, single-request mode for the current Windows user.
# After running: quit Ollama from the system tray and start it again.
$ErrorActionPreference = "Stop"
$vars = @{
    "OLLAMA_NO_CLOUD"          = "1"      # disable Ollama Cloud models (paid per token)
    "OLLAMA_NUM_PARALLEL"      = "1"      # one request at a time
    "OLLAMA_MAX_LOADED_MODELS" = "1"      # never keep Qwen and Gemma in VRAM together
    "OLLAMA_CONTEXT_LENGTH"    = "8192"
}
foreach ($k in $vars.Keys) {
    [Environment]::SetEnvironmentVariable($k, $vars[$k], "User")
    Write-Host "set $k=$($vars[$k])"
}
$hostVar = [Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User")
if ($hostVar -and $hostVar -notmatch "^(http://)?(127\.0\.0\.1|localhost)(:\d+)?$") {
    Write-Warning "OLLAMA_HOST=$hostVar is not loopback. Remove it so Ollama stays local."
}
Write-Host "Done. Restart Ollama (quit from the tray icon, then start it again)."
