# PharmacyOS ERP — sets up the LOCAL ERP server on this Windows 10/11 PC (run elevated).
#
# Normally started by the PharmacyOS ERP desktop app's setup ("This computer is the pharmacy server"),
# which asks Windows for administrator permission once and shows the progress this script writes to
#   %ProgramData%\PharmacyOS\Setup\state.json      (step, percent, message, status)
#   %ProgramData%\PharmacyOS\Logs\install.log       (full technical log)
#
# What it does (each step is skipped when already done, so it can always be run again):
#   1. checks Windows (build 19041+, 64-bit, virtualization, free disk); %ProgramData%\PharmacyOS is
#      restricted to SYSTEM, Administrators and this Windows user first;
#   2. turns on WSL2 — if Windows must restart, it registers itself to continue after the next sign-in
#      (no second permission prompt) and asks the app to show "Restart now";
#   3. downloads Ubuntu 24.04 (checked against the SHA-256 Ubuntu publishes) and imports it as the
#      "PharmacyOS" environment (systemd on);
#   4. runs deploy/server/install-server.sh inside it (MariaDB, Redis, pinned Frappe 16.36.1 / ERPNext
#      16.37.0, PharmacyOS ERP, the pharmacy and its owner account), relaying its progress;
#   5. registers the "PharmacyOS Server" task: the server starts with Windows, before anyone signs in,
#      and stays running;
#   6. reports the server address to the app.
#
# The pharmacy's details come from a request file written by the app (-RequestFile, JSON: pharmacy_name,
# pharmacy_name_ar, owner_full_name, owner_email, owner_password, phone, share_on_network). The file is
# deleted as soon as the owner account exists. Passwords never appear on a command line or in a log.
#
# Manual use (an IT person, without the app):
#   powershell -ExecutionPolicy Bypass -File install-server.ps1 -RequestFile C:\path\request.json
#
# Works in Windows PowerShell 5.1 (every Windows 10/11) and PowerShell 7.
[CmdletBinding()]
param(
	[string]$RequestFile = "",
	[switch]$Resume,
	[string]$AppExe = "",
	[string]$Site = "pharmacy.local",
	[int]$HttpPort = 0
)
$ErrorActionPreference = "Stop"
$Distro = "PharmacyOS"
$Root = Join-Path $env:ProgramData "PharmacyOS"
$SetupDir = Join-Path $Root "Setup"
$StateFile = Join-Path $SetupDir "state.json"
$LogFile = Join-Path $Root "Logs\install.log"
$ResumeTask = "PharmacyOS Setup (continue)"
$ServerTask = "PharmacyOS Server"
$RootfsUrl = "https://cloud-images.ubuntu.com/wsl/releases/24.04/current/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz"
New-Item -ItemType Directory -Force -Path $SetupDir, (Join-Path $Root "Logs"), (Join-Path $Root "Backups"), (Join-Path $Root "Sales"), (Join-Path $Root "Daily Reports"), (Join-Path $Root "Server") | Out-Null

# ------------------------------------------------------------------ state, log, helpers

$Steps = [ordered]@{
	check      = @{ n = 1; weight = 2; ar = "فحص الجهاز"; en = "Checking this computer" }
	wsl        = @{ n = 2; weight = 6; ar = "تفعيل بيئة الخادم (WSL2)"; en = "Turning on the server environment (WSL2)" }
	distro     = @{ n = 3; weight = 12; ar = "تنزيل نظام الخادم"; en = "Downloading the server system" }
	server     = @{ n = 4; weight = 70; ar = "تثبيت خادم الصيدلية"; en = "Installing the pharmacy server" }
	autostart  = @{ n = 5; weight = 5; ar = "التشغيل التلقائي مع Windows"; en = "Starting automatically with Windows" }
	finish     = @{ n = 6; weight = 5; ar = "التحقق النهائي"; en = "Final check" }
}
$ServerSteps = @{
	packages = "Installing system packages"; toolchain = "Installing tools"; database = "Setting up the database"
	app_release = "Copying PharmacyOS ERP"; bench = "Installing Frappe and ERPNext (longest step)"; site_config = "Configuring the pharmacy site"
	first_run = "Creating the pharmacy and the owner account"; production = "Setting up the web server"; finish = "Starting the services"
}

function Write-Log([string]$Text) {
	$line = "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $Text
	Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Write-State([string]$Step, [string]$Status, [string]$Message = "", [double]$Within = 0, [hashtable]$Extra = @{}) {
	$done = 0.0
	foreach ($k in $Steps.Keys) { if ($k -eq $Step) { break }; $done += $Steps[$k].weight }
	$percent = [math]::Min(100, [math]::Round($done + $Steps[$Step].weight * $Within))
	if ($Status -eq "done") { $percent = 100 }
	$state = [ordered]@{
		version = 1; step = $Step; step_number = $Steps[$Step].n; steps = $Steps.Count; percent = $percent
		status = $Status; title_ar = $Steps[$Step].ar; title_en = $Steps[$Step].en; message = $Message
		updated = (Get-Date).ToString("o"); log = $LogFile
	}
	foreach ($k in $Extra.Keys) { $state[$k] = $Extra[$k] }
	$tmp = "$StateFile.tmp"
	($state | ConvertTo-Json -Depth 4) | Set-Content -Path $tmp -Encoding UTF8
	Move-Item -Force $tmp $StateFile
	Write-Log "[$Step] $Status $Message"
}

function Fail([string]$Step, [string]$Message) {
	Write-State $Step "failed" $Message
	exit 1
}

# Runs a native program, logging its output. Windows PowerShell 5.1 turns native stderr into terminating
# errors under ErrorActionPreference=Stop, so it is relaxed for the call. Returns the exit code.
function Invoke-Native([string]$File, [string[]]$Arguments) {
	$old = $ErrorActionPreference
	$ErrorActionPreference = "Continue"
	try {
		& $File @Arguments 2>&1 | ForEach-Object { Write-Log ("  " + (([string]$_) -replace "`0", "")) }
		return $LASTEXITCODE
	} finally {
		$ErrorActionPreference = $old
	}
}

function Test-Admin {
	$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
	return $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Values reach bash base64-encoded (UTF-8), so quotes, spaces and Arabic text can never break the command.
function ConvertTo-B64([string]$Value) { return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$Value)) }

# C:\path\to\folder -> /mnt/c/path/to/folder (no script-block -replace: PowerShell 5.1 has none)
function ConvertTo-WslPath([string]$WindowsPath) {
	$full = (Resolve-Path $WindowsPath).Path
	return "/mnt/" + $full.Substring(0, 1).ToLower() + ($full.Substring(2) -replace "\\", "/")
}

# The bash script that runs install-server.sh with the pharmacy's values. It is started with
# "wsl.exe --exec bash -c <script>", so exactly one shell (that bash) reads it: without --exec, root's
# default shell would expand it first and bash would then re-read the decoded values as code. Each value
# is base64 (letters, digits, + / =: nothing a shell acts on) decoded into a plain assignment, which bash
# never splits or globs: spaces, quotes, $, #, ; and Arabic arrive byte for byte. The script has no double
# quote and no backslash (but in '\'' for a path with a quote), so it is one Windows argument as it is.
function ConvertTo-InstallScript([System.Collections.IDictionary]$Values, [string]$InstallScript) {
	$parts = @()
	foreach ($name in $Values.Keys) {
		if ($name -notmatch "^[A-Z_][A-Z0-9_]*$") { throw "Not a variable name: $name" }
		$parts += "$name=`$(printf %s $(ConvertTo-B64 $Values[$name]) | base64 -d); export $name"
	}
	$parts += "bash '" + ($InstallScript -replace "'", "'\''") + "' 2>&1"
	return $parts -join "; "
}

# The wsl.exe command line (ProcessStartInfo.Arguments) that runs that script as root in the environment.
function Get-InstallArguments([string]$DistroName, [System.Collections.IDictionary]$Values, [string]$InstallScript) {
	return "-d $DistroName --user root --exec bash -c `"$(ConvertTo-InstallScript $Values $InstallScript)`""
}

# The SHA-256 Ubuntu publishes for a download, from the SHA256SUMS file in the same folder (over HTTPS).
function Get-PublishedSha256([string]$Url) {
	$slash = $Url.LastIndexOf("/")
	$name = $Url.Substring($slash + 1)
	$sums = (Invoke-WebRequest ($Url.Substring(0, $slash + 1) + "SHA256SUMS") -UseBasicParsing).Content
	if ($sums -is [byte[]]) { $sums = [Text.Encoding]::ASCII.GetString($sums) }
	foreach ($line in ([string]$sums -split "`n")) {
		if ($line.Trim() -match "^([0-9a-fA-F]{64})\s+\*?(\S+)$" -and $Matches[2] -eq $name) { return $Matches[1].ToUpper() }
	}
	return ""
}

# %ProgramData%\PharmacyOS holds the pharmacy's backups, sales spreadsheets, daily reports, logs and the
# server environment. ProgramData lets every local user read it and create files in it, so the folder gets
# its own ACL: full control for SYSTEM, Administrators and this Windows user (who runs the PharmacyOS
# Server task and the app), nothing inherited from ProgramData. Whatever is already inside is given to
# Administrators and set to inherit exactly that, so nothing another user put there before (a fake backup,
# a "cached" Ubuntu image) keeps entries of its own. Well-known SIDs (any Windows language); links are never
# followed; the files in Server\wsl (the environment's disk, made by wsl --import) keep what WSL set on
# them. Safe to re-run (resume after a restart, repair): an already restricted folder is left as it is.
$DataOwnerSid = "S-1-5-32-544"   # BUILTIN\Administrators

function Test-DataFolderLocked([string[]]$Sids) {
	try {
		$acl = Get-Acl -LiteralPath $Root
		if (-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $DataOwnerSid) { return $false }
		$rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
		if (@($rules | Where-Object { $_.AccessControlType -ne "Allow" -or $Sids -notcontains $_.IdentityReference.Value }).Count) { return $false }
		foreach ($sid in $Sids) {
			$full = @($rules | Where-Object { $_.IdentityReference.Value -eq $sid -and $_.FileSystemRights -eq "FullControl" -and $_.InheritanceFlags -eq "ContainerInherit, ObjectInherit" -and $_.PropagationFlags -eq "None" })
			if (-not $full.Count) { return $false }
		}
		return $true
	} catch { return $false }
}

function Protect-DataItems([string]$Dir, [string[]]$Sids) {
	foreach ($item in @(Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue)) {
		if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Write-Log "Not followed (a link): $($item.FullName)"; continue }
		$clean = $false
		try {
			$acl = Get-Acl -LiteralPath $item.FullName
			$clean = (-not $acl.AreAccessRulesProtected) -and ($Sids -contains $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value) -and
				(@($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier])).Count -eq 0)
		} catch { $clean = $false }
		if (-not $clean) {
			Write-Log "Resetting the permissions of $($item.FullName)"
			Invoke-Native "icacls.exe" @($item.FullName, "/setowner", "*$DataOwnerSid", "/C", "/Q") | Out-Null
			Invoke-Native "icacls.exe" @($item.FullName, "/reset", "/C", "/Q") | Out-Null
		}
		if ($item.PSIsContainer -and $item.FullName -ne (Join-Path $Root "Server\wsl")) { Protect-DataItems $item.FullName $Sids }
	}
}

function Protect-DataFolder {
	$me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
	$sids = @(@("S-1-5-18", $DataOwnerSid, $me) | Select-Object -Unique)
	if ((Get-Item -LiteralPath $Root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
	if (-not (Test-DataFolderLocked $sids)) {
		Write-Log "Restricting $Root to SYSTEM, Administrators and $me"
		Invoke-Native "icacls.exe" @($Root, "/setowner", "*$DataOwnerSid", "/C", "/Q") | Out-Null
		Invoke-Native "icacls.exe" @($Root, "/reset", "/C", "/Q") | Out-Null   # drops entries anyone added to the folder itself
		$grants = @($sids | ForEach-Object { "*$($_):(OI)(CI)F" })
		Invoke-Native "icacls.exe" (@($Root, "/inheritance:r", "/grant:r") + $grants + @("/C", "/Q")) | Out-Null
	}
	Protect-DataItems $Root $sids
	return (Test-DataFolderLocked $sids)
}

function Test-DistroInstalled {
	$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
	try { $names = (& wsl.exe -l -q 2>$null) -replace "`0", "" } finally { $ErrorActionPreference = $old }
	return [bool]($names | Where-Object { $_.Trim() -eq $Distro })
}

function Register-Continuation {
	# after the restart Windows needs for WSL: continue at the next sign-in, elevated, without asking again
	$self = $PSCommandPath
	$taskArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$self`" -Resume -RequestFile `"$RequestFile`""
	if ($AppExe) { $taskArgs += " -AppExe `"$AppExe`"" }
	$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArgs
	$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
	$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
	Register-ScheduledTask -TaskName $ResumeTask -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
	if ($AppExe -and (Test-Path $AppExe)) {
		# and open PharmacyOS to show the progress
		Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "PharmacyOSSetup" -Value "`"$AppExe`" --resume-setup"
	}
}

function Unregister-Continuation {
	Unregister-ScheduledTask -TaskName $ResumeTask -Confirm:$false -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------------ the pharmacy's details

if (-not (Test-Admin)) { Write-Error "Run as administrator."; exit 5 }
Add-Content -Path $LogFile -Value "" -Encoding UTF8
Write-Log "===== PharmacyOS server setup $(if ($Resume) { '(continuing)' }) — $([Environment]::OSVersion.VersionString)"
$locked = $false
try { $locked = Protect-DataFolder } catch { Write-Log "Protecting $Root failed: $($_.Exception.Message)" }
if (-not $locked) { Fail "check" "The PharmacyOS data folder ($Root) could not be protected from other Windows users. Retry; if it fails again, send the log to support." }
$Bundle = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Version = (Get-Content (Join-Path $Bundle "VERSION") -ErrorAction SilentlyContinue | Select-Object -First 1)
$request = $null
if ($RequestFile -and (Test-Path $RequestFile)) {
	$request = Get-Content -Raw -Encoding UTF8 $RequestFile | ConvertFrom-Json
}
$serverDone = $false
if (Test-DistroInstalled) { $serverDone = ((Invoke-Native "wsl.exe" @("-d", $Distro, "--user", "root", "--exec", "test", "-f", "/var/lib/pharmacyos/install/first_run.done")) -eq 0) }
if (-not $request -and -not $serverDone) {
	Fail "check" "The pharmacy's details are missing. Start the setup again from PharmacyOS ERP."
}
if ($request -and -not $serverDone) {
	if (-not $request.pharmacy_name -or -not $request.owner_email -or ([string]$request.owner_password).Length -lt 8) {
		Fail "check" "The pharmacy name, the owner's email and a password of at least 8 characters are needed."
	}
}

# ------------------------------------------------------------------ 1. this computer

Write-State "check" "running" "Checking Windows"
$build = [Environment]::OSVersion.Version.Build
if (-not [Environment]::Is64BitOperatingSystem) { Fail "check" "PharmacyOS needs 64-bit Windows 10 or 11." }
if ($build -lt 19041) { Fail "check" "PharmacyOS needs Windows 10 version 2004 (build 19041) or newer. This PC has build $build. Install the Windows updates first." }
$freeGb = [math]::Round((Get-PSDrive -Name ($env:SystemDrive.Substring(0, 1))).Free / 1GB, 1)
if ($freeGb -lt 15) { Fail "check" "At least 15 GB of free disk space is needed on $env:SystemDrive (free: $freeGb GB)." }
# the web port is chosen once and kept (on a later run, port 80 may be PharmacyOS's own WSL forwarding)
$portFile = Join-Path $SetupDir "http-port.txt"
if ($HttpPort -le 0 -and (Test-Path $portFile)) { $HttpPort = [int](Get-Content $portFile | Select-Object -First 1) }
if ($HttpPort -le 0) {
	$HttpPort = 80
	$busy = Get-NetTCPConnection -State Listen -LocalPort 80 -ErrorAction SilentlyContinue
	if ($busy) { $HttpPort = 8780; Write-Log "Port 80 is used by another program on this PC: using port $HttpPort." }
}
Set-Content -Path $portFile -Value $HttpPort -Encoding ASCII

# ------------------------------------------------------------------ 2. WSL2

Write-State "wsl" "running" "Turning on WSL2"
$wslReady = $false
try { $wslReady = ((Invoke-Native "wsl.exe" @("--status")) -eq 0) } catch { $wslReady = $false }
$vmp = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue
$wslf = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
$pending = ($vmp -and $vmp.State -eq "EnablePending") -or ($wslf -and $wslf.State -eq "EnablePending")
if (-not $wslReady -or ($vmp -and $vmp.State -ne "Enabled") -or ($wslf -and $wslf.State -ne "Enabled")) {
	if (-not $pending) {
		Write-Log "wsl --install --no-distribution"
		$code = 1
		try { $code = Invoke-Native "wsl.exe" @("--install", "--no-distribution") } catch { $code = 1 }
		if ($code -ne 0) {
			# older Windows 10 builds: the inbox wsl.exe has no --no-distribution; turn the features on directly
			Write-Log "Enabling the Windows features directly"
			Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart -WarningAction SilentlyContinue | Out-Null
			Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart -WarningAction SilentlyContinue | Out-Null
		}
		$vmp = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue
		$wslf = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
		$pending = ($vmp -and $vmp.State -ne "Enabled") -or ($wslf -and $wslf.State -ne "Enabled")
	}
	if ($pending) {
		Register-Continuation
		Write-State "wsl" "reboot_required" "Windows must restart to finish turning on WSL2. Setup continues by itself after you sign in again."
		exit 3010
	}
}
Unregister-Continuation
Invoke-Native "wsl.exe" @("--update") | Out-Null
Invoke-Native "wsl.exe" @("--set-default-version", "2") | Out-Null

# ------------------------------------------------------------------ 3. the PharmacyOS environment

if (-not (Test-DistroInstalled)) {
	Write-State "distro" "running" "Downloading Ubuntu 24.04 (about 350 MB)"
	$rootfs = Join-Path $Root "Server\ubuntu-24.04-rootfs.tar.gz"
	$ProgressPreference = "SilentlyContinue"   # the progress bar makes Invoke-WebRequest very slow in 5.1
	[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
	# the image is imported only when its SHA-256 matches the one Ubuntu publishes for it: a copy already
	# in the Server folder (an interrupted setup) is checked like a fresh download, never trusted by size
	$expected = ""
	try { $expected = Get-PublishedSha256 $RootfsUrl } catch { Fail "distro" "Ubuntu could not be downloaded. Check the internet connection and retry. ($($_.Exception.Message))" }
	if (-not $expected) { Fail "distro" "Ubuntu's checksum list (SHA256SUMS) has no entry for the server image. Retry later; if it fails again, send the log to support." }
	$verified = (Test-Path $rootfs) -and ((Get-FileHash -LiteralPath $rootfs -Algorithm SHA256).Hash -eq $expected)
	for ($try = 1; -not $verified -and $try -le 2; $try++) {
		if (Test-Path $rootfs) { Write-Log "$rootfs does not match the published SHA-256: downloading it again"; Remove-Item -Force $rootfs }
		try { Invoke-WebRequest $RootfsUrl -OutFile "$rootfs.part" -UseBasicParsing } catch { Fail "distro" "Ubuntu could not be downloaded. Check the internet connection and retry. ($($_.Exception.Message))" }
		Move-Item -Force "$rootfs.part" $rootfs
		$verified = (Get-FileHash -LiteralPath $rootfs -Algorithm SHA256).Hash -eq $expected
	}
	if (-not $verified) {
		Remove-Item -Force $rootfs -ErrorAction SilentlyContinue
		Fail "distro" "The downloaded Ubuntu image did not match its published SHA-256 checksum, so it was not used. Check the internet connection and retry; if it fails again, send the log to support."
	}
	Write-Log "Ubuntu image verified (SHA-256 $expected)"
	Write-State "distro" "running" "Creating the PharmacyOS environment" 0.8
	Invoke-Native "wsl.exe" @("--import", $Distro, (Join-Path $Root "Server\wsl"), $rootfs, "--version", "2") | Out-Null
	if (-not (Test-DistroInstalled)) { Fail "distro" "The PharmacyOS environment could not be created (wsl --import failed). Is virtualization turned on in the BIOS?" }
}
# systemd inside the environment (the services start at boot); keep it running while Windows is on
Invoke-Native "wsl.exe" @("-d", $Distro, "--user", "root", "--", "bash", "-c", "printf '[boot]\nsystemd=true\n[user]\ndefault=root\n' > /etc/wsl.conf") | Out-Null
$wslconfig = Join-Path $env:USERPROFILE ".wslconfig"
if (-not (Test-Path $wslconfig)) {
	$lines = @("[wsl2]", "vmIdleTimeout=-1")
	if ($request -and $request.share_on_network -and $build -ge 22621) { $lines += "networkingMode=mirrored" }
	Set-Content -Path $wslconfig -Value $lines -Encoding ASCII
}
Invoke-Native "wsl.exe" @("--terminate", $Distro) | Out-Null

# ------------------------------------------------------------------ 4. the pharmacy server

Write-State "server" "running" "Preparing"
$src = ConvertTo-WslPath $Bundle
$values = [ordered]@{ SITE = $Site; HTTP_PORT = [string]$HttpPort; PHARMACYOS_DATA_DIR = (ConvertTo-WslPath $Root) }
if ($request -and -not $serverDone) {
	$values["PHARMACY_NAME"] = [string]$request.pharmacy_name
	$values["PHARMACY_NAME_AR"] = [string]$request.pharmacy_name_ar
	$values["OWNER_EMAIL"] = [string]$request.owner_email
	$values["OWNER_PASSWORD"] = [string]$request.owner_password
	$values["OWNER_FULL_NAME"] = [string]$request.owner_full_name
	$values["PHARMACY_PHONE"] = [string]$request.phone
}
$total = $ServerSteps.Count
$psi = New-Object Diagnostics.ProcessStartInfo
$psi.FileName = "wsl.exe"
$psi.Arguments = Get-InstallArguments $Distro $values "$src/deploy/server/install-server.sh"
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.StandardOutputEncoding = [Text.Encoding]::UTF8
$psi.CreateNoWindow = $true
$proc = [Diagnostics.Process]::Start($psi)
while (-not $proc.StandardOutput.EndOfStream) {
	$line = $proc.StandardOutput.ReadLine()
	if ($line -match "^##PHARMACYOS-STEP (\d+) (\d+) (\S+)") {
		$n = [int]$Matches[1]; $total = [int]$Matches[2]; $key = $Matches[3]
		$label = $ServerSteps[$key]; if (-not $label) { $label = $key }
		Write-State "server" "running" "$label ($n/$total)" (($n - 1) / $total)
	} elseif ($line -notmatch "OWNER_PASSWORD|ADMIN_PASSWORD|DB_ROOT_PASSWORD") {
		Write-Log "  $line"
	}
}
$proc.WaitForExit()
if ($proc.ExitCode -ne 0) { Fail "server" "Installing the pharmacy server failed (code $($proc.ExitCode)). Retry; if it fails again, send the log to support." }
if ($RequestFile -and (Test-Path $RequestFile)) { Remove-Item -Force $RequestFile }   # the owner exists: forget the password

# ------------------------------------------------------------------ 5. start with Windows

Write-State "autostart" "running" "Registering the PharmacyOS Server task"
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro --user root --exec /opt/pharmacyos/bin/pharmacyos-server run"
$triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"))
# S4U: runs at boot for this Windows account (the one WSL registered the environment for), signed in or not
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
	-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $ServerTask -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $ServerTask
if ($request -and $request.share_on_network) {
	New-NetFirewallRule -DisplayName "PharmacyOS ERP (pharmacy network)" -Direction Inbound -Protocol TCP -LocalPort $HttpPort -Profile Private -Action Allow -ErrorAction SilentlyContinue | Out-Null
}

# ------------------------------------------------------------------ 6. done

Write-State "finish" "running" "Waiting for the pharmacy server"
$url = if ($HttpPort -eq 80) { "http://127.0.0.1" } else { "http://127.0.0.1:$HttpPort" }
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
	try { if ((Invoke-RestMethod -Uri "$url/api/method/ping" -TimeoutSec 5 -UseBasicParsing).message -eq "pong") { $ok = $true; break } } catch { }
	Start-Sleep -Seconds 2
}
if (-not $ok) { Fail "finish" "The pharmacy server was installed but does not answer at $url. Restart the computer; if it still does not answer, send the log to support." }
Unregister-Continuation
Write-State "finish" "done" "PharmacyOS is ready." 1 @{ server_url = $url; version = $Version; http_port = $HttpPort }
exit 0
