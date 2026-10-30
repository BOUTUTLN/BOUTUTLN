# Removes the BOUTUTLN scheduled tasks.
Get-ScheduledTask -TaskName "BOUTUTLN-*" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
Write-Host "BOUTUTLN tasks removed."
