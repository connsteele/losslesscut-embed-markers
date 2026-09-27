$ErrorActionPreference = 'Stop'

try {
    $repoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).ProviderPath.TrimEnd('\')
    $pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $pythonPath = (Get-Command python -ErrorAction Stop).Source
    }
    # The helper finishes before any environment files are removed.
    $planJson = & $pythonPath -B (Join-Path $repoRoot 'llc_markers\maintenance.py') reset-plan
    if ($LASTEXITCODE -ne 0) { throw 'Setup reset checks failed; nothing was removed.' }
    $plan = ($planJson -join "`n") | ConvertFrom-Json
    if ($plan.repo -ne $repoRoot) { throw 'Unexpected repository in reset plan.' }
    $allowed = @('.venv', 'losslesscut_embed_markers.egg-info', '__pycache__',
                 'llc_markers\__pycache__', 'tests\__pycache__') |
        ForEach-Object { [IO.Path]::GetFullPath((Join-Path $repoRoot $_)) }
    # Validate the complete allowlist and every descendant before any deletion.
    foreach ($targetPath in @($plan.targets)) {
        $resolved = (Resolve-Path -LiteralPath $targetPath).ProviderPath
        if ($resolved -ne $targetPath -or $resolved -notin $allowed -or
            -not $resolved.StartsWith($repoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Reset target is outside the allowed setup paths: $targetPath"
        }
        $item = Get-Item -LiteralPath $resolved -Force
        if (-not $item.PSIsContainer) { throw "Expected a setup directory: $resolved" }
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked directory: $resolved" }
        if (@(Get-ChildItem -LiteralPath $resolved -Recurse -Force -Attributes ReparsePoint).Count -gt 0) {
            throw "Reset directory contains linked files: $resolved"
        }
    }
    foreach ($targetPath in @($plan.targets)) {
        Write-Host "Removing setup files: $targetPath"
        Remove-Item -LiteralPath $targetPath -Recurse -Force
    }
    Write-Host 'Setup reset complete. Run setup.cmd to rebuild the Python environment.'
    Write-Host 'Your configuration, footage, backups, reports, and processing state were preserved.'
    exit 0
} catch {
    Write-Host "Setup reset stopped: $($_.Exception.Message)"
    exit 1
}
