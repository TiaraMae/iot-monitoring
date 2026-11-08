<#
.SYNOPSIS
    Inside-view outage recorder: one CSV row per run (run it every 60 s).
.DESCRIPTION
    READ-ONLY probing. Records which layer is healthy at each minute so the
    next ERR_CONNECTION_REFUSED window (seen from outside, e.g. UptimeRobot)
    can be matched to a cause:
      adapters (Ethernet / Wi-Fi) -> IPv4 -> number of default routes ->
      gateway ping + gateway MAC (a changing MAC = IP conflict / ARP issue) ->
      internet ping -> DNS -> nginx :443 listening -> local HTTPS ->
      backend :5000 -> CPU / RAM / uptime.
    A MISSING row (gap in the CSV) means the PC itself was down/asleep/rebooting.
    Changes nothing on the machine. Output: C:\IoTDiag\watchdog_<yyyyMMdd>.csv
    (files older than 30 days are deleted). Pure ASCII on purpose (PS 5.1).

    INSTALL (elevated PowerShell on the mini-PC, after copying this file to C:\IoTDiag\):
        $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\IoTDiag\net-watchdog.ps1"
        $t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
        $s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 55) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $p = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName "IoT-NetWatchdog" -Action $a -Trigger $t -Settings $s -Principal $p

    CHECK:   Get-Content C:\IoTDiag\watchdog_$(Get-Date -f yyyyMMdd).csv -Tail 5
    REMOVE:  Unregister-ScheduledTask -TaskName "IoT-NetWatchdog" -Confirm:$false
#>

param(
    [string]$OutDir = "C:\IoTDiag",
    [string]$Gateway = "",   # empty = auto-detect from the lowest-metric default route
    [string]$InternetIp = "1.1.1.1",
    [string]$DnsName = "www.google.com",
    [int]$KeepDays = 30
)

$ErrorActionPreference = "SilentlyContinue"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

function Ping1($ip) {
    # One ICMP echo, 1 s timeout; returns round-trip ms or "FAIL".
    $p = New-Object System.Net.NetworkInformation.Ping
    try {
        $r = $p.Send($ip, 1000)
        if ($r.Status -eq "Success") { return [string]$r.RoundtripTime }
        return "FAIL"
    } catch { return "FAIL" } finally { $p.Dispose() }
}

function HttpCode($url) {
    $code = & curl.exe -s -k -o NUL -w "%{http_code}" --max-time 5 $url 2>$null
    if (-not $code) { return "ERR" }
    return $code
}

$now = Get-Date
if (-not $Gateway) {
    $Gateway = (Get-NetRoute -DestinationPrefix "0.0.0.0/0" |
        Sort-Object { $_.RouteMetric + $_.InterfaceMetric } |
        Select-Object -First 1).NextHop
    if (-not $Gateway) { $Gateway = "none" }
}
$adapters = Get-NetAdapter -Physical
$wifi = $adapters | Where-Object { $_.NdisPhysicalMedium -eq 9 -or $_.InterfaceDescription -match 'Wi-?Fi|Wireless|802\.11' }
$eth  = $adapters | Where-Object { $wifi -notcontains $_ }

$ethStatus  = ($eth  | ForEach-Object { "$($_.Name)=$($_.Status)" }) -join "|"
$wifiStatus = ($wifi | ForEach-Object { "$($_.Name)=$($_.Status)" }) -join "|"
if (-not $wifiStatus) { $wifiStatus = "none" }

$ethIp = ($eth | ForEach-Object { (Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4).IPAddress }) -join "|"
$defRoutes = @(Get-NetRoute -DestinationPrefix "0.0.0.0/0" | Where-Object { $_.State -ne "Unreachable" }).Count

$gwPing  = Ping1 $Gateway
$gwMac   = (Get-NetNeighbor -IPAddress $Gateway -AddressFamily IPv4 | Select-Object -First 1).LinkLayerAddress
if (-not $gwMac) { $gwMac = "none" }
$netPing = Ping1 $InternetIp

$dnsOk = "FAIL"
try { if ([System.Net.Dns]::GetHostAddresses($DnsName).Count -gt 0) { $dnsOk = "OK" } } catch { }

$listen443 = if (Get-NetTCPConnection -State Listen -LocalPort 443) { "yes" } else { "NO" }
$conn443   = @(Get-NetTCPConnection -LocalPort 443 | Where-Object { $_.State -ne "Listen" }).Count
$https     = HttpCode "https://localhost/"
$backend   = HttpCode "http://127.0.0.1:5000/login"

$cpu = [int](Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$os  = Get-CimInstance Win32_OperatingSystem
$freeMb = [int]($os.FreePhysicalMemory / 1024)
$uptimeMin = [int]((Get-Date) - $os.LastBootUpTime).TotalMinutes

$row = [PSCustomObject]@{
    time            = $now.ToString("yyyy-MM-dd HH:mm:ss")
    eth_status      = $ethStatus
    wifi_status     = $wifiStatus
    eth_ipv4        = $ethIp
    default_routes  = $defRoutes
    gw_ping_ms      = $gwPing
    gw_mac          = $gwMac
    internet_ping_ms= $netPing
    dns             = $dnsOk
    nginx_listen443 = $listen443
    conn_443        = $conn443
    local_https     = $https
    backend_5000    = $backend
    cpu_pct         = $cpu
    free_ram_mb     = $freeMb
    uptime_min      = $uptimeMin
}

$file = Join-Path $OutDir ("watchdog_" + $now.ToString("yyyyMMdd") + ".csv")
if (Test-Path $file) {
    $row | ConvertTo-Csv -NoTypeInformation | Select-Object -Skip 1 | Out-File -FilePath $file -Append -Encoding ascii
} else {
    $row | ConvertTo-Csv -NoTypeInformation | Out-File -FilePath $file -Encoding ascii
}

# Retention: drop watchdog files older than $KeepDays days.
Get-ChildItem -Path $OutDir -Filter "watchdog_*.csv" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } |
    Remove-Item -Force
