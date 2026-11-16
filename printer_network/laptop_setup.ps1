# Direct cable laptop <-> Core One: gives the laptop's Ethernet port a fixed address and lets the
# printer's force data (UDP 8514) in. Run in a Terminal opened as administrator:
#   powershell -ExecutionPolicy Bypass -File printer_network\laptop_setup.ps1
# Wi-Fi is not touched (internet keeps working). Undo with laptop_undo.ps1.

$Adapter = "Ethernet"          # name in Get-NetAdapter
$LaptopIp = "192.168.50.1"
$Prefix = 24                   # 255.255.255.0
$RuleName = "Prusa metrics UDP 8514"

$ErrorActionPreference = "Stop"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Start this from a Terminal opened as administrator." -ForegroundColor Red
    exit 1
}

Write-Host "Fixed address $LaptopIp on '$Adapter' (no gateway, no DNS)..."
Set-NetIPInterface -InterfaceAlias $Adapter -Dhcp Disabled
Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false
New-NetIPAddress -InterfaceAlias $Adapter -IPAddress $LaptopIp -PrefixLength $Prefix | Out-Null

Write-Host "Firewall rule '$RuleName' (inbound UDP 8514, force data from the printer)..."
if (-not (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol UDP `
        -LocalPort 8514 -Profile Any | Out-Null
}

Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 | Format-Table InterfaceAlias, IPAddress, PrefixLength
Write-Host "Done. Printer should be at 192.168.50.2 (test: ping 192.168.50.2)." -ForegroundColor Green
