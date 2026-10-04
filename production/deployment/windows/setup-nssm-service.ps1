#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the production Flask backend as a Windows service using NSSM
.DESCRIPTION
    Uses NSSM (Non-Sucking Service Manager) to run Waitress as a Windows service.
    The service auto-starts on boot and restarts on failure.

    Adapted from iot_thesis_v4 for the production app: Waitress serves
    "run:app" (run.py exposes the Flask app AND acquires the backend.lock
    single-process guard, so a second instance exits instead of double-
    processing MQTT messages).
#>

param(
    [string]$AppDir = "C:\IoTMonitoring_Production",
    [string]$ServiceName = "IoT-Backend",
    [string]$NssmPath = "C:\nssm\nssm.exe",
    [string]$PythonPath = "python",
    [int]$Threads = 4,
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"

Write-Host "=== IoT Backend Service Setup (production) ===" -ForegroundColor Cyan
Write-Host "App Directory: $AppDir" -ForegroundColor White
Write-Host "Service Name:  $ServiceName" -ForegroundColor White
Write-Host ""

# Verify paths
if (-not (Test-Path $AppDir)) {
    Write-Error "App directory not found: $AppDir"
    exit 1
}

if (-not (Test-Path "$AppDir\run.py")) {
    Write-Error "run.py not found in $AppDir - is this the production checkout?"
    exit 1
}

if (-not (Test-Path $NssmPath)) {
    Write-Error "NSSM not found at: $NssmPath"
    Write-Host "Please download NSSM from https://nssm.cc/download and extract to C:\nssm\" -ForegroundColor Yellow
    exit 1
}

# Ensure log directory exists
$logsDir = "$AppDir\logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
    Write-Host "Created log directory: $logsDir" -ForegroundColor Green
}

# Remove existing service if present
$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Removing existing service: $ServiceName" -ForegroundColor Yellow
    & $NssmPath remove $ServiceName confirm 2>$null
    Start-Sleep -Seconds 2
}

# Install new service
Write-Host "Installing service: $ServiceName" -ForegroundColor Cyan

$waitressArgs = "-m waitress --port=$Port --threads=$Threads run:app"

& $NssmPath install $ServiceName $PythonPath $waitressArgs
& $NssmPath set $ServiceName DisplayName "IoT Monitoring Backend (Production)"
& $NssmPath set $ServiceName Description "Production Flask backend for IoT Monitoring System (Waitress, run:app)"
& $NssmPath set $ServiceName Application $PythonPath
& $NssmPath set $ServiceName AppDirectory $AppDir
& $NssmPath set $ServiceName AppParameters $waitressArgs
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout "$logsDir\service.out.log"
& $NssmPath set $ServiceName AppStderr "$logsDir\service.err.log"
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateBytes 10485760  # 10 MB

# Configure restart on failure
& $NssmPath set $ServiceName AppRestartDelay 5000  # 5 seconds

# Start service
Write-Host "Starting service..." -ForegroundColor Cyan
Start-Service -Name $ServiceName
Start-Sleep -Seconds 3

# Verify
$service = Get-Service -Name $ServiceName
if ($service.Status -eq 'Running') {
    Write-Host ""
    Write-Host "=== Service installed and running ===" -ForegroundColor Green
    Write-Host "Service: $ServiceName" -ForegroundColor White
    Write-Host "Status:  $($service.Status)" -ForegroundColor White
    Write-Host "URL:     http://127.0.0.1:$Port" -ForegroundColor White
} else {
    Write-Warning "Service installed but not running. Status: $($service.Status)"
    Write-Host "Check logs at: $logsDir\" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "NOTE: only ONE backend process may run. This service holds" -ForegroundColor Yellow
Write-Host "      $AppDir\backend.lock - starting run.py manually while the" -ForegroundColor Yellow
Write-Host "      service runs will exit immediately (by design)." -ForegroundColor Yellow
Write-Host ""
Write-Host "Management commands:" -ForegroundColor Cyan
Write-Host "  Start:   Start-Service $ServiceName" -ForegroundColor White
Write-Host "  Stop:    Stop-Service $ServiceName" -ForegroundColor White
Write-Host "  Restart: Restart-Service $ServiceName" -ForegroundColor White
Write-Host "  Remove:  & '$NssmPath' remove $ServiceName confirm" -ForegroundColor White
