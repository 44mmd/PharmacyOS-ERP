# PharmacyOS ERP — removes the PharmacyOS server environment from this PC (run elevated).
# DESTROYS the pharmacy database inside the environment. Backups in %ProgramData%\PharmacyOS\Backups are
# kept. Takes a final backup first unless -SkipBackup is given. Used by the desktop app's uninstaller only
# when the person removing PharmacyOS explicitly chooses to remove the server too.
param([switch]$SkipBackup)
$ErrorActionPreference = "Continue"
$Distro = "PharmacyOS"
$log = Join-Path $env:ProgramData "PharmacyOS\Logs\uninstall.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Log($t) { Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $t) }
$names = ((& wsl.exe -l -q 2>$null) -replace "`0", "") | ForEach-Object { $_.Trim() }
if ($names -contains $Distro) {
	if (-not $SkipBackup) {
		Log "final backup"
		& wsl.exe -d $Distro --user root --exec /opt/pharmacyos/bin/pharmacyos-server backup 2>&1 | ForEach-Object { Log "  $_" }
	}
	& wsl.exe --terminate $Distro 2>&1 | Out-Null
	& wsl.exe --unregister $Distro 2>&1 | ForEach-Object { Log "  $_" }
	Log "environment removed"
}
foreach ($task in "PharmacyOS Server", "PharmacyOS Setup (continue)") { Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue }
Remove-NetFirewallRule -DisplayName "PharmacyOS ERP (pharmacy network)" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $env:ProgramData "PharmacyOS\Server\wsl"), (Join-Path $env:ProgramData "PharmacyOS\Setup") -ErrorAction SilentlyContinue
Log "done (backups kept in $env:ProgramData\PharmacyOS\Backups)"
