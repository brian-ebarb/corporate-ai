# Corporate AI Dashboard - Start Script (Windows PowerShell)
param([switch]$Quiet)

$ErrorActionPreference = 'Stop'
$Root     = Split-Path $MyInvocation.MyCommand.Path -Resolve
$LogDir   = Join-Path $Root "logs"
$PidsFile = Join-Path $Root ".pids"

Write-Host ""
Write-Host "  Corporate AI Dashboard - Starting Up"
Write-Host ""

# Prerequisites
if (-not (Test-Path (Join-Path $Root "main.py")))              { Write-Error "main.py not found. Run from corporate-ai folder."; exit 1 }
if (-not (Test-Path (Join-Path $Root "relay\server.js")))      { Write-Error "relay\server.js not found.";      exit 1 }
if (-not (Test-Path (Join-Path $Root "dashboard\index.html"))) { Write-Error "dashboard\index.html not found."; exit 1 }

# Port configuration
Write-Host "  Configure ports (press Enter to accept defaults):"
Write-Host ""

function Read-Port($label, $default) {
    $val = Read-Host "    $label port [$default]"
    if ([string]::IsNullOrWhiteSpace($val)) { return $default }
    if ($val -notmatch '^\d+$') {
        Write-Warning "Invalid port '$val', using default $default"
        return $default
    }
    return $val
}

$portBackend   = Read-Port "Backend  " "8000"
$portRelay     = Read-Port "Relay    " "3001"
$portDashboard = Read-Port "Dashboard" "3000"
Write-Host ""

# Stop any already-running services
if (Test-Path $PidsFile) {
    Write-Host "  Stopping existing services first..."
    & "$Root\server_stop.ps1" -Quiet
    Start-Sleep 1
}

# Create logs directory; clear pid file
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
"" | Set-Content $PidsFile

# Install relay deps if absent
$relayModules = Join-Path $Root "relay\node_modules"
if (-not (Test-Path $relayModules)) {
    Write-Host "  Installing relay dependencies..."
    Push-Location (Join-Path $Root "relay")
    & cmd /c "npm install"
    Pop-Location
    Write-Host ""
}

# All processes launched via "cmd /c" so .cmd scripts (npx, node, npm) resolve correctly.
# taskkill /T in server_stop kills the cmd wrapper and its child process together.

# [1/3] Backend — PORT env var sets the uvicorn listen port
Write-Host "  [1/3] Starting backend  (port $portBackend)..."
$p1 = Start-Process cmd `
    -ArgumentList "/c set PORT=$portBackend && python main.py" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput (Join-Path $LogDir "backend.log") `
    -RedirectStandardError  (Join-Path $LogDir "backend.err") `
    -NoNewWindow -PassThru
"backend=$($p1.Id)" | Add-Content $PidsFile
Write-Host "         PID $($p1.Id) -> logs\backend.log"
Start-Sleep 2

# [2/3] Relay — RELAY_PORT sets listen port; CA_PORT tells relay where backend is
Write-Host "  [2/3] Starting relay    (port $portRelay)..."
$p2 = Start-Process cmd `
    -ArgumentList "/c set RELAY_PORT=$portRelay && set CA_PORT=$portBackend && node server.js" `
    -WorkingDirectory (Join-Path $Root "relay") `
    -RedirectStandardOutput (Join-Path $LogDir "relay.log") `
    -RedirectStandardError  (Join-Path $LogDir "relay.err") `
    -NoNewWindow -PassThru
"relay=$($p2.Id)" | Add-Content $PidsFile
Write-Host "         PID $($p2.Id) -> logs\relay.log"
Start-Sleep 2

# [3/3] Dashboard
Write-Host "  [3/3] Starting dashboard (port $portDashboard)..."
$p3 = Start-Process cmd `
    -ArgumentList "/c npx serve . -p $portDashboard" `
    -WorkingDirectory (Join-Path $Root "dashboard") `
    -RedirectStandardOutput (Join-Path $LogDir "dashboard.log") `
    -RedirectStandardError  (Join-Path $LogDir "dashboard.err") `
    -NoNewWindow -PassThru
"dashboard=$($p3.Id)" | Add-Content $PidsFile
Write-Host "         PID $($p3.Id) -> logs\dashboard.log"

Write-Host ""
Write-Host "  All services started. PIDs saved to .pids"
Write-Host ""
Write-Host "  Backend:   http://localhost:$portBackend"
Write-Host "  Relay:     http://localhost:$portRelay"
Write-Host "  Dashboard: http://localhost:$portDashboard"
Write-Host ""
Write-Host "  Open http://localhost:$portDashboard in your browser."
Write-Host "  Run server_stop.bat to stop all services."
Write-Host ""
