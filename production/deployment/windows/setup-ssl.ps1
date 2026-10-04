#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Obtain and configure Let's Encrypt SSL certificate for Windows
.DESCRIPTION
    Installs Certbot for Windows, obtains SSL certificate for the subdomain,
    and configures Nginx to use it. Also sets up auto-renewal via Task Scheduler.
#>

param(
    [string]$Domain = "iotmonitor.sgu.ac.id",
    [string]$CertbotPath = "C:\Certbot\bin\certbot.exe",
    [string]$NginxPath = "C:\nginx\nginx.exe",
    [string]$NginxConfDir = "C:\nginx\conf",
    [string]$Email = "tiara.mae@student.sgu.ac.id"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Let's Encrypt SSL Setup ===" -ForegroundColor Cyan
Write-Host "Domain: $Domain" -ForegroundColor White
Write-Host ""

# Check if Certbot is installed
if (-not (Test-Path $CertbotPath)) {
    Write-Host "Certbot not found at: $CertbotPath" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please install Certbot for Windows:" -ForegroundColor Cyan
    Write-Host "1. Download from: https://dl.eff.org/certbot-beta-installer-win_amd64.exe" -ForegroundColor White
    Write-Host "2. Run the installer (installs to C:\Certbot\ by default)" -ForegroundColor White
    Write-Host "3. Re-run this script" -ForegroundColor White
    Write-Host ""
    Write-Error "Certbot not installed. Aborting."
}

# Check if Nginx is installed
if (-not (Test-Path $NginxPath)) {
    Write-Error "Nginx not found at: $NginxPath. Please install Nginx for Windows first."
}

Write-Host "Obtaining SSL certificate for $Domain..." -ForegroundColor Cyan
Write-Host "Make sure port 80 is accessible from the internet for ACME validation." -ForegroundColor Yellow
Write-Host ""

# Create Nginx temp config for standalone challenge (port 80 must be free)
$tempConf = @"
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
"@

# Backup existing nginx config
$nginxConf = "$NginxConfDir\nginx.conf"
$backupConf = "$NginxConfDir\nginx.conf.backup.$(Get-Date -Format yyyyMMddHHmmss)"
if (Test-Path $nginxConf) {
    Copy-Item $nginxConf $backupConf
    Write-Host "Backed up nginx.conf to: $backupConf" -ForegroundColor Green
}

# Write temp minimal config for certbot standalone
$tempConf | Out-File -FilePath "$NginxConfDir\nginx.conf" -Encoding UTF8

# Restart Nginx with temp config
Write-Host "Restarting Nginx with temporary config..." -ForegroundColor Cyan
& $NginxPath -s reload 2>$null
Start-Sleep -Seconds 2
if (-not $?) {
    & $NginxPath
    Start-Sleep -Seconds 3
}

# Obtain certificate using webroot (nginx serves the challenge)
Write-Host "Running Certbot..." -ForegroundColor Cyan
& $CertbotPath certonly `
    --webroot `
    -w C:/nginx/html `
    -d $Domain `
    --agree-tos `
    --non-interactive `
    --email $Email `
    --no-eff-email

if ($LASTEXITCODE -ne 0) {
    Write-Error "Certbot failed. Check that port 80 is accessible and the domain resolves to this server's public IP."
}

Write-Host ""
Write-Host "Certificate obtained successfully!" -ForegroundColor Green
Write-Host "Certificate path: C:\Certbot\live\$Domain\" -ForegroundColor White

# Verify certificate files exist
$certPath = "C:\Certbot\live\$Domain\fullchain.pem"
$keyPath = "C:\Certbot\live\$Domain\privkey.pem"
if (-not (Test-Path $certPath) -or -not (Test-Path $keyPath)) {
    Write-Error "Certificate files not found. Something went wrong."
}

# Set up auto-renewal via Task Scheduler
# IMPORTANT: The certificate uses the webroot plugin, so nginx MUST be running
# during renewal to serve the ACME challenge from C:/nginx/html/.well-known/.
# Do NOT stop nginx in a pre-hook - that breaks validation and the cert expires
# silently (this exact mistake caused the 2026-09-02 outage). The deploy-hook
# reloads nginx only AFTER a successful renewal so the new cert is loaded.
$taskName = "Certbot-AutoRenew-$Domain"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing renewal task: $taskName" -ForegroundColor Yellow
}

$action = New-ScheduledTaskAction -Execute $CertbotPath -Argument "renew --quiet --logs-dir C:\Certbot\log-system --deploy-hook `"$NginxPath -s reload`""
# Note: --logs-dir keeps the SYSTEM task's log separate from any manual admin runs;
# certbot locks its log file to the last account that ran it (fatal Permission
# denied for the other account otherwise).
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -WakeToRun
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Auto-renew Let's Encrypt SSL certificate for $Domain"

Write-Host ""
Write-Host "Auto-renewal task created: $taskName" -ForegroundColor Green
Write-Host "Renewal runs daily at 03:00 AM (nginx must be running - do not stop it)" -ForegroundColor White
Write-Host "Verify the task once: Start-ScheduledTask -TaskName `"$taskName`", then check C:\Certbot\logs\letsencrypt.log" -ForegroundColor White
Write-Host ""
Write-Host "=== SSL Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Copy deployment/windows/nginx-iot-monitor.conf to C:\nginx\conf\sites-enabled\" -ForegroundColor White
Write-Host "2. Update server_name in nginx-iot-monitor.conf to: $Domain" -ForegroundColor White
Write-Host "3. Reload Nginx: C:\nginx\nginx.exe -s reload" -ForegroundColor White
Write-Host "4. Test HTTPS: https://$Domain" -ForegroundColor White
