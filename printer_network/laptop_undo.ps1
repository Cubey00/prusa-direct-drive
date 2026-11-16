# Undo laptop_setup.ps1: Ethernet back to automatic (DHCP), firewall rule removed.
# Run in a Terminal opened as administrator:
#   powershell -ExecutionPolicy Bypass -File printer_network\laptop_undo.ps1

$Adapter = "Ethernet"
$RuleName = "Prusa metrics UDP 8514"

Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false
Set-NetIPInterface -InterfaceAlias $Adapter -Dhcp Enabled
Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
Write-Host "Ethernet is back on DHCP and the firewall rule is removed." -ForegroundColor Green
