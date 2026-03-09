# Corporate AI Dashboard - Stop Script (Windows PowerShell)
param([switch]$Quiet)

$Root     = Split-Path $MyInvocation.MyCommand.Path -Resolve
$PidsFile = Join-Path $Root ".pids"

if (-not $Quiet) {
    Write-Host ""
    Write-Host "  Corporate AI Dashboard - Stopping..."
    Write-Host ""
}

if (-not (Test-Path $PidsFile)) {
    if (-not $Quiet) { Write-Host "  .pids not found - nothing to stop." }
    exit 0
}

$lines = Get-Content $PidsFile | Where-Object { $_ -match "=" }
foreach ($line in $lines) {
    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) { continue }
    $name   = $parts[0].Trim()
    $pidStr = $parts[1].Trim()
    if ($pidStr -notmatch "^\d+$") { continue }
    $procId = [int]$pidStr

    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) {
        if (-not $Quiet) { Write-Host "  Stopping $name (PID $procId)..." }
        & taskkill /T /F /PID $procId 2>&1 | Out-Null
    } else {
        if (-not $Quiet) { Write-Host "  $name (PID $procId) already stopped." }
    }
}

Remove-Item $PidsFile -ErrorAction SilentlyContinue

if (-not $Quiet) {
    Write-Host ""
    Write-Host "  Done."
    Write-Host ""
}
