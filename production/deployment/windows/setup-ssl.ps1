#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Obtain and configure Let's Encrypt SSL certificate using simple-acme
.DESCRIPTION
    Certbot DISCONTINUED Windows support in Feb 2024, so this script uses
    simple-acme (the community drop-in replacement for win-acme, made by the
    same author): https://github.com/simple-acme/simple-acme

    - Validates via the filesystem (webroot) plugin: nginx must be running on
      port 80 serving C:\nginx\html for the ACME challenge.
    - Exports PEM files for nginx to C:\ssl\<domain>\  (cert+chain and key).
    - Installs a post-renewal hook that reloads nginx.
    - Creates its OWN daily renewal Scheduled Task on first run
      ("simple-acme renew ...") -- no manual renewal task needed.

    NEVER stop nginx around renewal: webroot validation requires it alive
    (that exact mistake caused the 2026-09-02 v4 outage).
#>

param(
    [string]$Domain = "iotmonitor.sgu.ac.id",
    [string]$WacsPath = "C:\win-acme\wacs.exe",
    [string]$NginxDir = "C:\nginx",
    [string]$Email = "tiara.mae@student.sgu.ac.id",
    [string]$PemDir = "C:\ssl\iotmonitor.sgu.ac.id"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Let's Encrypt SSL Setup (simple-acme) ===" -ForegroundColor Cyan
Write-Host "Domain: $Domain" -ForegroundColor White
Write-Host ""

# Check simple-acme is installed (extract the release zip to C:\win-acme)
if (-not (Test-Path $WacsPath)) {
    Write-Host "simple-acme not found at: $WacsPath" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Install it:" -ForegroundColor Cyan
    Write-Host "1. Download simple-acme win-x64 (pluggable) from:" -ForegroundColor White
    Write-Host "   https://github.com/simple-acme/simple-acme/releases/latest" -ForegroundColor White
    Write-Host "2. Extract the zip so that $WacsPath exists" -ForegroundColor White
    Write-Host "3. Re-run this script" -ForegroundColor White
    Write-Host ""
    Write-Error "simple-acme not installed. Aborting."
}

# Check nginx is installed
if (-not (Test-Path "$NginxDir\nginx.exe")) {
    Write-Error "Nginx not found at: $NginxDir\nginx.exe. Please install Nginx for Windows first."
}

Write-Host "Obtaining SSL certificate for $Domain..." -ForegroundColor Cyan
Write-Host "Port 80 must be reachable from the internet (ACME webroot validation)." -ForegroundColor Yellow
Write-Host ""

# Create nginx temp config that only serves the ACME challenge on port 80
$tempConf = @"
worker_processes  1;
events {
    worker_connections  1024;
}
http {
    include       mime.types;
    default_type  application/octet-stream;
    server {
        listen 80;
    server_name $Domain;
    location /.well-known/acme-challenge/ {
        root C:/nginx/html;
    }
        location / {
            return 200 "OK";
        }
    }
}
"@

# Backup existing nginx config
$nginxConf = "$NginxDir\conf\nginx.conf"
$backupConf = "$NginxDir\conf\nginx.conf.backup.$(Get-Date -Format yyyyMMddHHmmss)"
if (Test-Path $nginxConf) {
    Copy-Item $nginxConf $backupConf
    Write-Host "Backed up nginx.conf to: $backupConf" -ForegroundColor Green
}

# Write temp minimal config for the validation window
$tempConf | Out-File -FilePath $nginxConf -Encoding ascii

# (Re)start nginx with the temp config. -p pins the prefix so logs/conf
# resolve relative to the nginx dir even when launched from elsewhere.
Write-Host "Restarting nginx with temporary config..." -ForegroundColor Cyan
& "$NginxDir\nginx.exe" -p "$NginxDir/" -s reload 2>$null
Start-Sleep -Seconds 2
if (-not $?) {
    & "$NginxDir\nginx.exe" -p "$NginxDir/"
    Start-Sleep -Seconds 3
}

# nginx reload hook used after (re)newal: run from the nginx dir so it finds
# its prefix/config regardless of the scheduler's working directory.
$reloadScript = "$NginxDir\reload-after-cert.cmd"
@"
@echo off
cd /d $NginxDir
nginx.exe -s reload
"@ | Out-File -FilePath $reloadScript -Encoding ascii

# Make sure the ACME webroot path exists for the filesystem validation
New-Item -ItemType Directory -Path "$NginxDir\html\.well-known\acme-challenge" -Force | Out-Null

# Obtain certificate: manual host, filesystem validation via nginx webroot,
# PEM export for nginx, and reload nginx after every successful renewal.
Write-Host "Running simple-acme (wacs)..." -ForegroundColor Cyan
& $WacsPath `
    --source manual `
    --host $Domain `
    --validation filesystem `
    --webroot "$NginxDir\html" `
    --store pemfiles `
    --pemfilespath $PemDir `
    --installation script `
    --script $reloadScript `
    --accepttos `
    --emailaddress $Email

if ($LASTEXITCODE -ne 0) {
    Write-Error "simple-acme failed. Check that port 80 is reachable from the internet and the domain resolves to this server's public IP."
}

Write-Host ""
Write-Host "Certificate obtained successfully!" -ForegroundColor Green
Write-Host "PEM files at: $PemDir\" -ForegroundColor White

# Verify the files nginx will use exist
$chainPem = "$PemDir\$Domain-chain.pem"
$keyPem   = "$PemDir\$Domain-key.pem"
if (-not (Test-Path $chainPem) -or (-not (Test-Path $keyPem))) {
    Write-Error "Expected PEM files not found ($chainPem / $keyPem). Check the simple-acme output above."
}

# Confirm the auto-renewal scheduled task exists (created by simple-acme)
$renewTask = Get-ScheduledTask | Where-Object { $_.TaskName -match "simple-acme|win-acme" }
if ($renewTask) {
    Write-Host "Auto-renewal task present: $($renewTask.TaskName)" -ForegroundColor Green
} else {
    Write-Warning "No simple-acme renewal task found. Check simple-acme's output -- it should create one named 'simple-acme renew (...)'."
}

Write-Host ""
Write-Host "=== SSL Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Renewal: simple-acme runs its own scheduled task; nginx is reloaded" -ForegroundColor White
Write-Host "automatically after each successful renewal via $reloadScript" -ForegroundColor White
Write-Host "(webroot validation needs nginx running -- never stop it for renewal)." -ForegroundColor Yellow
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Copy deployment/windows/nginx.conf OVER C:\nginx\conf\nginx.conf:" -ForegroundColor White
Write-Host "     copy C:\IoTMonitoring\production\deployment\windows\nginx.conf C:\nginx\conf\nginx.conf" -ForegroundColor White
Write-Host "2. Test config: C:\nginx\nginx.exe -p C:\nginx\ -t" -ForegroundColor White
Write-Host "3. Reload nginx: C:\nginx\nginx.exe -p C:\nginx\ -s reload" -ForegroundColor White
Write-Host "4. Test HTTPS: https://$Domain" -ForegroundColor White
Write-Host ""
Write-Host "Force-renewal check (run once to be safe):" -ForegroundColor Yellow
Write-Host "  & '$WacsPath' --renew --baseuri https://acme-v02.api.letsencrypt.org/" -ForegroundColor White
