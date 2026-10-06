# PharmacyOS ERP — single-PC server setup on Windows 10/11 (run as Administrator).
#
# Creates the "PharmacyOS" WSL2 environment (Ubuntu 24.04) that runs the pharmacy database and
# services, starts it at boot, and makes it reachable from this PC and (optionally) the pharmacy
# LAN. Pharmacy backups go to C:\ProgramData\PharmacyOS. Requires virtualization enabled in BIOS.
# Installing needs the internet once (Ubuntu, packages, PharmacyOS ERP); the pharmacy then runs
# without it.
#
#   .\install-server.ps1 -Site pharmacy.local -PharmacyName "Al Noor Pharmacy" -PharmacyNameAr "صيدلية النور" `
#       -OwnerEmail owner@example.com [-ShareOnNetwork]
#   (the administrator and owner passwords are asked for; they are never written to disk)
#
# Works in Windows PowerShell 5.1 (the version every Windows 10/11 has) and PowerShell 7.
param(
	[Parameter(Mandatory = $true)][string]$Site,
	[Parameter(Mandatory = $true)][string]$PharmacyName,
	[string]$PharmacyNameAr = "",
	[Parameter(Mandatory = $true)][string]$OwnerEmail,
	[Parameter(Mandatory = $true)][SecureString]$AdminPassword,
	[Parameter(Mandatory = $true)][SecureString]$OwnerPassword,
	[switch]$ShareOnNetwork
)
$ErrorActionPreference = "Stop"
$Distro = "PharmacyOS"
$Root = "$env:ProgramData\PharmacyOS"
New-Item -ItemType Directory -Force -Path "$Root\Server", "$Root\Backups", "$Root\Sales", "$Root\Daily Reports", "$Root\Logs" | Out-Null

function Get-PlainText([SecureString]$Secure) {
	$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
	try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
	finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# Values reach bash base64-encoded (UTF-8), so quotes, spaces and Arabic text in names or passwords
# can never break or inject into the command line.
function ConvertTo-B64([string]$Value) {
	return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
}

# C:\path\to\folder -> /mnt/c/path/to/folder (no script-block -replace: PowerShell 5.1 has none)
function ConvertTo-WslPath([string]$WindowsPath) {
	$full = (Resolve-Path $WindowsPath).Path
	$drive = $full.Substring(0, 1).ToLower()
	return "/mnt/$drive" + ($full.Substring(2) -replace "\\", "/")
}

if ((Get-PlainText $OwnerPassword).Length -lt 8) { throw "The owner password must have at least 8 characters." }

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

$bytes = New-Object byte[] 24
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$dbRoot = [Convert]::ToBase64String($bytes) -replace "[+/=]", "x"
$src = ConvertTo-WslPath "$PSScriptRoot\..\.."
$values = [ordered]@{
	SITE              = $Site
	ADMIN_PASSWORD    = (Get-PlainText $AdminPassword)
	DB_ROOT_PASSWORD  = $dbRoot
	PHARMACY_NAME     = $PharmacyName
	PHARMACY_NAME_AR  = $PharmacyNameAr
	OWNER_EMAIL       = $OwnerEmail
	OWNER_PASSWORD    = (Get-PlainText $OwnerPassword)
	PHARMACYOS_DATA_DIR = "/mnt/c/ProgramData/PharmacyOS"
}
$exports = ($values.GetEnumerator() | ForEach-Object { "export $($_.Key)=`$(printf %s '$(ConvertTo-B64 $_.Value)' | base64 -d)" }) -join "; "
wsl -d $Distro --user root -- bash -c "$exports; bash '$src/deploy/server/install-server.sh'"
if ($LASTEXITCODE -ne 0) { throw "The PharmacyOS server installation failed (exit code $LASTEXITCODE). See the output above." }
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
