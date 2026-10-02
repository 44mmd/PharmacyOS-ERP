# PharmacyOS ERP — single-PC server setup on Windows 10/11 (run as Administrator).
#
# Creates the "PharmacyOS" WSL2 environment (Ubuntu 24.04) that runs the pharmacy database and
# services, starts it at boot, and makes it reachable from this PC and (optionally) the pharmacy
# LAN. Pharmacy backups go to C:\ProgramData\PharmacyOS. Requires virtualization enabled in BIOS.
param(
	[Parameter(Mandatory = $true)][string]$Site,
	[Parameter(Mandatory = $true)][SecureString]$AdminPassword,
	[switch]$ShareOnNetwork
)
$ErrorActionPreference = "Stop"
$Distro = "PharmacyOS"
$Root = "$env:ProgramData\PharmacyOS"
New-Item -ItemType Directory -Force -Path "$Root\Server", "$Root\Backups", "$Root\Sales", "$Root\Daily Reports", "$Root\Logs" | Out-Null

wsl --install --no-distribution 2>$null
if (-not (wsl -l -q | Select-String -SimpleMatch $Distro)) {
	$rootfs = "$Root\Server\ubuntu-24.04-rootfs.tar.gz"
	if (-not (Test-Path $rootfs)) {
		Invoke-WebRequest "https://cloud-images.ubuntu.com/wsl/releases/24.04/current/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz" -OutFile $rootfs
	}
	wsl --import $Distro "$Root\Server\wsl" $rootfs --version 2
}

# systemd inside the environment; keep it running while Windows is on
wsl -d $Distro --user root -- bash -c "printf '[boot]\nsystemd=true\n' > /etc/wsl.conf"
$wslconfig = "$env:USERPROFILE\.wslconfig"
$lines = @("[wsl2]", "vmIdleTimeout=-1")
if ($ShareOnNetwork) { $lines += "networkingMode=mirrored" }
Set-Content -Path $wslconfig -Value $lines
wsl --shutdown

$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($AdminPassword))
$dbRoot = [Convert]::ToBase64String((1..24 | ForEach-Object { Get-Random -Maximum 256 }))
$src = (Resolve-Path "$PSScriptRoot\..\..").Path -replace "\\", "/" -replace "^([A-Za-z]):", { "/mnt/" + $_.Groups[1].Value.ToLower() }
wsl -d $Distro --user root -- bash -c "SITE='$Site' ADMIN_PASSWORD='$plain' DB_ROOT_PASSWORD='$dbRoot' PHARMACYOS_DATA_DIR=/mnt/c/ProgramData/PharmacyOS bash '$src/deploy/server/install-server.sh'"
Set-Content -Path "$Root\Server\db-root.txt" -Value $dbRoot   # readable by Administrators only (ACL below)
icacls "$Root\Server\db-root.txt" /inheritance:r /grant:r "Administrators:F" | Out-Null

# start the PharmacyOS environment at boot (before anyone signs in)
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro --user root --exec /opt/pharmacyos/bin/pharmacyos-server start"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName "PharmacyOS Server" -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null

if ($ShareOnNetwork) {
	New-NetFirewallRule -DisplayName "PharmacyOS ERP (pharmacy network)" -Direction Inbound -Protocol TCP -LocalPort 80 -Profile Private -Action Allow | Out-Null
}
Write-Host "PharmacyOS server installed. Open 'PharmacyOS ERP' and choose 'This computer is the pharmacy server'."
