# One-shot repair of the lab NIC (InterfaceIndex 4, static 172.22.1.10/24).
# Restart adapter first (clears stale duplicate detection); if 172.22.1.10 is
# still not Preferred, re-apply the same static config (gw 172.22.1.20, DNS 172.22.1.3).
# ASCII only (script is read by Windows PowerShell 5.1 without BOM).
$ErrorActionPreference = 'Continue'
$log = 'D:\MCPTools\instrument-mcp\net_fix_result.txt'
function Log($m) { $m | Out-File $log -Append -Encoding utf8 }

"=== run at $(Get-Date) ===" | Out-File $log -Encoding utf8

Restart-NetAdapter -InterfaceIndex 4 -Confirm:$false
Start-Sleep -Seconds 12

$addrs = Get-NetIPAddress -InterfaceIndex 4 -AddressFamily IPv4 -ErrorAction SilentlyContinue
Log ("after restart: " + (($addrs | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength) state=$($_.AddressState)" }) -join ' ; '))

$main = $addrs | Where-Object { $_.IPAddress -eq '172.22.1.10' }
if (-not $main -or $main.AddressState -ne 'Preferred') {
    Log 're-applying static config'
    Remove-NetIPAddress -InterfaceIndex 4 -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetRoute -InterfaceIndex 4 -Confirm:$false -ErrorAction SilentlyContinue
    New-NetIPAddress -InterfaceIndex 4 -IPAddress 172.22.1.10 -PrefixLength 24 -DefaultGateway 172.22.1.20 | Out-Null
    Set-DnsClientServerAddress -InterfaceIndex 4 -ServerAddresses 172.22.1.3
    Start-Sleep -Seconds 8
    $addrs = Get-NetIPAddress -InterfaceIndex 4 -AddressFamily IPv4 -ErrorAction SilentlyContinue
    Log ("after reapply: " + (($addrs | ForEach-Object { "$($_.IPAddress) state=$($_.AddressState)" }) -join ' ; '))
}

$dup = $addrs | Where-Object { $_.AddressState -eq 'Duplicate' }
if ($dup) { Log ('STILL DUPLICATE: ' + (($dup | ForEach-Object { $_.IPAddress }) -join ',')) }
else { Log 'OK: no duplicate address' }

ping -n 2 -w 1500 172.22.1.4 | Out-File $log -Append -Encoding utf8
arp -a | Select-String '172.22.1.' | Out-File $log -Append -Encoding utf8
Log '=== done ==='
