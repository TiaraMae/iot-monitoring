#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Windows Firewall hardening for IoT Monitoring System
.DESCRIPTION
    Creates firewall rules to allow HTTP/HTTPS from external,
    block internal services (PostgreSQL, Flask) from external,
    and allow outbound MQTTS/HTTPS.
#>

$ErrorActionPreference = "Stop"

Write-Host "=== IoT Monitoring Firewall Setup ===" -ForegroundColor Cyan
Write-Host ""

# Remove existing rules if present (idempotent)
$rules = @("IoT-Monitor-HTTP-In", "IoT-Monitor-HTTPS-In", "IoT-Monitor-Postgres-Block", "IoT-Monitor-Flask-Block", "IoT-Monitor-MQTTS-Out", "IoT-Monitor-HTTPS-Out")
foreach ($rule in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
    if ($existing) {
        Remove-NetFirewallRule -DisplayName $rule
        Write-Host "Removed existing rule: $rule" -ForegroundColor Yellow
    }
}

# Inbound allow HTTP (80)
New-NetFirewallRule `
    -DisplayName "IoT-Monitor-HTTP-In" `
    -Direction Inbound `
    -LocalPort 80 `
    -Protocol TCP `
    -Action Allow `
    -Profile Any `
    -Description "Allow inbound HTTP for IoT Monitoring dashboard"
Write-Host "[+] Created: Allow inbound HTTP (80)" -ForegroundColor Green

# Inbound allow HTTPS (443)
New-NetFirewallRule `
    -DisplayName "IoT-Monitor-HTTPS-In" `
    -Direction Inbound `
    -LocalPort 443 `
    -Protocol TCP `
    -Action Allow `
    -Profile Any `
    -Description "Allow inbound HTTPS for IoT Monitoring dashboard"
Write-Host "[+] Created: Allow inbound HTTPS (443)" -ForegroundColor Green

# Inbound block PostgreSQL (5432) -- local only
New-NetFirewallRule `
    -DisplayName "IoT-Monitor-Postgres-Block" `
    -Direction Inbound `
    -LocalPort 5432 `
    -Protocol TCP `
    -Action Block `
    -Profile Any `
    -Description "Block external access to PostgreSQL"
Write-Host "[+] Created: Block inbound PostgreSQL (5432)" -ForegroundColor Green

# Inbound block Flask direct (5000) -- must go through Nginx
New-NetFirewallRule `
    -DisplayName "IoT-Monitor-Flask-Block" `
    -Direction Inbound `
    -LocalPort 5000 `
    -Protocol TCP `
    -Action Block `
    -Profile Any `
    -Description "Block external direct access to Flask backend"
Write-Host "[+] Created: Block inbound Flask (5000)" -ForegroundColor Green

# Outbound allow MQTTS (8883)
New-NetFirewallRule `
    -DisplayName "IoT-Monitor-MQTTS-Out" `
    -Direction Outbound `
    -RemotePort 8883 `
    -Protocol TCP `
    -Action Allow `
    -Profile Any `
    -Description "Allow outbound MQTTS to EMQX Cloud"
Write-Host "[+] Created: Allow outbound MQTTS (8883)" -ForegroundColor Green

# Outbound allow HTTPS (443)
New-NetFirewallRule `
    -DisplayName "IoT-Monitor-HTTPS-Out" `
    -Direction Outbound `
    -RemotePort 443 `
    -Protocol TCP `
    -Action Allow `
    -Profile Any `
    -Description "Allow outbound HTTPS for Certbot/Discord webhooks"
Write-Host "[+] Created: Allow outbound HTTPS (443)" -ForegroundColor Green

Write-Host ""
Write-Host "=== Firewall Setup Complete ===" -ForegroundColor Cyan
Write-Host "Active Rules:" -ForegroundColor White
Get-NetFirewallRule -DisplayName "IoT-Monitor-*" | Select-Object DisplayName, Direction, Action | Format-Table
