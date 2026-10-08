#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install Nginx as a Windows service using NSSM (auto-start on boot)
.DESCRIPTION
    Nginx for Windows does not install itself as a service. This script wraps
    it with NSSM so it survives reboots and restarts on failure — required for
    the daily 03:00 Certbot renewal (webroot validation needs nginx running;
    stopping it in a renewal pre-hook caused the 2026-09-02 v4 outage).
#>

param(
    [string]$NginxDir = "C:\nginx",
    [string]$NginxExe = "C:\nginx\nginx.exe",
    [string]$ServiceName = "IoT-Nginx",
    [string]$NssmPath = "C:\nssm\nssm.exe"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Nginx Service Setup ===" -ForegroundColor Cyan
Write-Host "Nginx:  $NginxExe" -ForegroundColor White
Write-Host "Service: $ServiceName" -ForegroundColor White
Write-Host ""

if (-not (Test-Path $NginxExe)) {
    Write-Error "Nginx not found at: $NginxExe. Extract nginx for Windows to C:\nginx\ first."
}

if (-not (Test-Path $NssmPath)) {
    Write-Error "NSSM not found at: $NssmPath. Download from https://nssm.cc/download and extract to C:\nssm\."
}

# Remove existing service if present
$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Removing existing service: $ServiceName" -ForegroundColor Yellow
    & $NssmPath remove $ServiceName confirm 2>$null
    Start-Sleep -Seconds 2
}

# Install new service. NOTE: nginx -s reload/quit signal a running master via
# its pid file, so for reloads use "C:\nginx\nginx.exe -s reload" from an
# elevated shell as before; the service manages start/stop/restart-on-failure.
Write-Host "Installing service: $ServiceName" -ForegroundColor Cyan

& $NssmPath install $ServiceName $NginxExe
& $NssmPath set $ServiceName DisplayName "IoT Monitoring Nginx (Production)"
& $NssmPath set $ServiceName Description "Reverse proxy / TLS termination for the IoT Monitoring dashboard. Must stay running for Certbot webroot renewal."
& $NssmPath set $ServiceName Application $NginxExe
& $NssmPath set $ServiceName AppDirectory $NginxDir
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout "$NginxDir\logs\service.out.log"
& $NssmPath set $ServiceName AppStderr "$NginxDir\logs\service.err.log"
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateBytes 10485760  # 10 MB
& $NssmPath set $ServiceName AppRestartDelay 3000     # 3 seconds

Write-Host "Starting service..." -ForegroundColor Cyan
Start-Service -Name $ServiceName
Start-Sleep -Seconds 3

$service = Get-Service -Name $ServiceName
if ($service.Status -eq 'Running') {
    Write-Host ""
    Write-Host "=== Nginx service installed and running ===" -ForegroundColor Green
} else {
    Write-Warning "Service installed but not running. Status: $($service.Status)"
    Write-Host "Check nginx config syntax first: $NginxExe -t" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Management commands:" -ForegroundColor Cyan
Write-Host "  Restart:  Restart-Service $ServiceName" -ForegroundColor White
Write-Host "  Reload cfg: $NginxExe -s reload   (no downtime)" -ForegroundColor White
Write-Host "  Remove:   & '$NssmPath' remove $ServiceName confirm" -ForegroundColor White
Write-Host ""
Write-Host "NOTE: keep this service running 24/7 — the Certbot renewal task" -ForegroundColor Yellow
Write-Host "      (03:00 daily) validates via webroot served by nginx." -ForegroundColor Yellow
