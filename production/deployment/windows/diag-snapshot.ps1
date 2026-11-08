<#
.SYNOPSIS
    READ-ONLY evidence snapshot for the random ERR_CONNECTION_REFUSED outages.
.DESCRIPTION
    Collects uptime/reboots, adapters/routes, link flaps, IP conflicts, Wi-Fi
    events, NIC power saving, Windows Update, service restarts, listeners,
    load and log tails into ONE text file. It changes NOTHING on the machine:
    no service, adapter, firewall, or config is touched.

    Run (elevated PowerShell, so the System/Security logs are readable):
        powershell.exe -ExecutionPolicy Bypass -File C:\IoTDiag\diag-snapshot.ps1
    Output: C:\IoTDiag\snapshot_<yyyyMMdd_HHmm>.txt
    Copy that file + C:\nginx\logs\access.log + C:\nginx\logs\error.log back
    to the laptop (AnyDesk file transfer) for analysis.

    Pure ASCII on purpose: PowerShell 5.1 reads BOM-less files as ANSI.
#>

param(
    [int]$Days = 14,
    [string]$OutDir = "C:\IoTDiag",
    [string]$NginxDir = "C:\nginx",
    [string]$AppDir = "C:\IoTMonitoring\production"
)

$ErrorActionPreference = "Continue"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$out = Join-Path $OutDir "snapshot_$stamp.txt"
$since = (Get-Date).AddDays(-$Days)

function Section($title) { "`r`n==================== $title ====================" }

function Get-Events($filter, $match) {
    try {
        $ev = Get-WinEvent -FilterHashtable $filter -ErrorAction Stop
        if ($match) { $ev = $ev | Where-Object { $_.Message -match $match } }
        if (-not $ev) { return "(no matching events)" }
        $ev | Sort-Object TimeCreated | ForEach-Object {
            $msg = ($_.Message -replace "\s+", " ")
            if ($msg.Length -gt 220) { $msg = $msg.Substring(0, 220) }
            "{0:yyyy-MM-dd HH:mm:ss}  id={1,-5} {2,-40} {3}" -f $_.TimeCreated, $_.Id, $_.ProviderName, $msg
        }
    } catch {
        "(no events / log unavailable: $($_.Exception.Message))"
    }
}

function Tail($path, $n) {
    if (Test-Path $path) { Get-Content -Path $path -Tail $n } else { "(missing: $path)" }
}

& {
    Section "SNAPSHOT"
    "Taken:     $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  (window: last $Days days)"
    "Computer:  $env:COMPUTERNAME"
    "TimeZone:  $((Get-TimeZone).Id)"
    $os = Get-CimInstance Win32_OperatingSystem
    "LastBoot:  $($os.LastBootUpTime)"
    "Uptime:    $((Get-Date) - $os.LastBootUpTime)"

    Section "REBOOTS / SHUTDOWNS / CRASHES (41,1074,6005,6006,6008)"
    Get-Events @{LogName='System'; Id=41,1074,6005,6006,6008; StartTime=$since} $null

    Section "SLEEP / RESUME (Power-Troubleshooter 1, Kernel-Power 42/107)"
    Get-Events @{LogName='System'; Id=1,42,107; StartTime=$since} 'sleep|resume|standby|hibernat'

    Section "ADAPTERS"
    Get-NetAdapter | Format-Table -AutoSize Name, InterfaceDescription, Status, LinkSpeed, MacAddress | Out-String -Width 250
    Get-NetIPConfiguration | ForEach-Object {
        "{0,-30} IPv4={1,-18} Gateway={2,-16} DNS={3}" -f $_.InterfaceAlias,
            (($_.IPv4Address | ForEach-Object { $_.IPAddress }) -join ","),
            (($_.IPv4DefaultGateway | ForEach-Object { $_.NextHop }) -join ","),
            (($_.DNSServer | Where-Object { $_.AddressFamily -eq 2 } | ForEach-Object { $_.ServerAddresses }) -join ",")
    }

    Section "DEFAULT ROUTES (more than one active = dual-gateway problem)"
    Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Format-Table -AutoSize ifIndex, InterfaceAlias, NextHop, RouteMetric, InterfaceMetric | Out-String -Width 250

    Section "IP INTERFACE DHCP/STATIC"
    Get-NetIPInterface -AddressFamily IPv4 | Format-Table -AutoSize InterfaceAlias, Dhcp, ConnectionState, InterfaceMetric | Out-String -Width 250

    Section "NIC POWER MANAGEMENT (AllowComputerToTurnOffDevice should be Disabled)"
    try { Get-NetAdapterPowerManagement -ErrorAction Stop | Format-List Name, AllowComputerToTurnOffDevice, DeviceSleepOnDisconnect, WakeOnMagicPacket | Out-String -Width 250 }
    catch { "(unavailable: $($_.Exception.Message))" }

    Section "NETWORK CONNECT / DISCONNECT (NetworkProfile 10000=connected 10001=disconnected)"
    Get-Events @{LogName='Microsoft-Windows-NetworkProfile/Operational'; Id=10000,10001; StartTime=$since} $null

    Section "IP ADDRESS CONFLICT (Tcpip 4199) + DHCP"
    Get-Events @{LogName='System'; ProviderName='Tcpip'; StartTime=$since} $null
    Get-Events @{LogName='System'; ProviderName='Microsoft-Windows-Dhcp-Client'; StartTime=$since} $null

    Section "NIC DRIVER / LINK EVENTS (System log, network-ish providers)"
    try {
        $ev = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$since} -ErrorAction Stop |
            Where-Object { $_.ProviderName -match 'e1|e2|rt|Realtek|Intel|NDIS|Netwtw|netadapter|Link|b57|ixgb|igb|r8' -and $_.ProviderName -notmatch 'Kernel-General' }
        if ($ev) {
            $ev | Sort-Object TimeCreated | ForEach-Object {
                $msg = ($_.Message -replace "\s+", " "); if ($msg.Length -gt 220) { $msg = $msg.Substring(0, 220) }
                "{0:yyyy-MM-dd HH:mm:ss}  id={1,-5} {2,-40} {3}" -f $_.TimeCreated, $_.Id, $_.ProviderName, $msg
            }
        } else { "(no matching events)" }
    } catch { "(unavailable: $($_.Exception.Message))" }

    Section "WI-FI (WLAN-AutoConfig 8001=connected 8003=disconnected)"
    Get-Events @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'; Id=8001,8003; StartTime=$since} $null

    Section "WINDOWS UPDATE INSTALLS (WindowsUpdateClient 19/20/43)"
    Get-Events @{LogName='System'; ProviderName='Microsoft-Windows-WindowsUpdateClient'; StartTime=$since} $null
    try {
        $wu = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -ErrorAction Stop
        "ActiveHoursStart=$($wu.ActiveHoursStart) ActiveHoursEnd=$($wu.ActiveHoursEnd)"
    } catch { "(active hours not readable)" }

    Section "IoT SERVICES"
    Get-Service IoT-Backend, IoT-Nginx -ErrorAction SilentlyContinue | Format-Table -AutoSize Name, Status, StartType | Out-String -Width 250
    Get-Process nginx, python, AnyDesk -ErrorAction SilentlyContinue |
        Format-Table -AutoSize Id, ProcessName, StartTime, CPU, @{n='WS_MB';e={[int]($_.WorkingSet64/1MB)}} | Out-String -Width 250

    Section "SERVICE START/STOP/CRASH EVENTS mentioning IoT or nginx (7031,7034,7036)"
    Get-Events @{LogName='System'; ProviderName='Service Control Manager'; Id=7031,7034,7036; StartTime=$since} 'IoT|nginx|Nginx'

    Section "LISTENERS 80/443/5000/5432"
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in 80,443,5000,5432 } |
        Format-Table -AutoSize LocalAddress, LocalPort, OwningProcess | Out-String -Width 250

    Section "TCP CONNECTIONS ON 443 BY STATE (flood check)"
    Get-NetTCPConnection -LocalPort 443 -ErrorAction SilentlyContinue | Group-Object State |
        Format-Table -AutoSize Count, Name | Out-String -Width 250
    "Top remote IPs on 443:"
    Get-NetTCPConnection -LocalPort 443 -ErrorAction SilentlyContinue | Group-Object RemoteAddress |
        Sort-Object Count -Descending | Select-Object -First 10 | Format-Table -AutoSize Count, Name | Out-String -Width 250

    Section "ESTABLISHED CONNECTIONS BY LOCAL ADDRESS (which interface carries traffic)"
    Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Group-Object LocalAddress |
        Format-Table -AutoSize Count, Name | Out-String -Width 250

    Section "LOAD"
    $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    "CPU load now: $cpu %"
    "RAM: free $([int]($os.FreePhysicalMemory/1024)) MB of $([int]($os.TotalVisibleMemorySize/1024)) MB"
    Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
        Format-Table -AutoSize DeviceID, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}} | Out-String -Width 250

    Section "POWER PLAN"
    powercfg /getactivescheme

    Section "LOG FILE SIZES"
    Get-ChildItem "$NginxDir\logs", "$AppDir\logs" -File -ErrorAction SilentlyContinue |
        Format-Table -AutoSize FullName, @{n='MB';e={[math]::Round($_.Length/1MB,2)}}, LastWriteTime | Out-String -Width 250

    Section "NGINX error.log (last 300 lines)"
    Tail "$NginxDir\logs\error.log" 300

    Section "NGINX service.err.log (last 50 lines)"
    Tail "$NginxDir\logs\service.err.log" 50

    Section "BACKEND service.out.log (last 300 lines)"
    Tail "$AppDir\logs\service.out.log" 300

    Section "BACKEND service.err.log (last 100 lines)"
    Tail "$AppDir\logs\service.err.log" 100

    Section "END"
} *>&1 | Out-File -FilePath $out -Encoding ascii -Width 300

Write-Host "Snapshot written: $out" -ForegroundColor Green
Write-Host "Copy back with AnyDesk: $out, $NginxDir\logs\access.log, $NginxDir\logs\error.log" -ForegroundColor Yellow
