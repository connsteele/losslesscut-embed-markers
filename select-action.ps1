$ErrorActionPreference = 'Stop'
try {
    while ($true) {
        [Console]::Write('Choose 1, 2, 3, 4, or 5, then press Enter: ')
        $selection = [Console]::ReadLine()
        if ($null -eq $selection) { exit 15 }
        # Use dedicated codes so a PowerShell startup/script failure (code 1)
        # cannot be mistaken for a request to start Preview.
        if ($selection.Trim() -cmatch '^[1-5]$') { exit (10 + [int]$selection.Trim()) }
        Write-Host 'Enter one number from 1 to 5. No action was started.'
    }
} catch {
    Write-Host "Could not read your selection: $($_.Exception.Message)"
    exit 16
}
