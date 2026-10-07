# PharmacyOS ERP — can this PC run the pharmacy server? (no administrator rights needed)
# Prints one JSON object: { ok, checks: [ { key, ok, level, value } ] }. The desktop setup shows it
# before asking Windows for permission to install. "error" blocks the setup; "warn" is advice.
$ErrorActionPreference = "SilentlyContinue"
$checks = New-Object System.Collections.ArrayList
function Add-Check([string]$Key, [bool]$Ok, [string]$Level, $Value) { [void]$checks.Add([ordered]@{ key = $Key; ok = $Ok; level = $Level; value = $Value }) }

$os = Get-CimInstance Win32_OperatingSystem
$build = [Environment]::OSVersion.Version.Build
Add-Check "windows" ($build -ge 19041 -and [Environment]::Is64BitOperatingSystem) "error" "$($os.Caption) (build $build)"
$ramGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
Add-Check "memory" ($ramGb -ge 7.5) "warn" "$ramGb GB"
if ($ramGb -lt 3.5) { $checks[$checks.Count - 1].level = "error" }
$freeGb = [math]::Round((Get-PSDrive -Name ($env:SystemDrive.Substring(0, 1))).Free / 1GB, 1)
Add-Check "disk" ($freeGb -ge 15) "error" "$freeGb GB"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$hyper = Get-CimInstance Win32_ComputerSystem
# virtualization: enabled in firmware, or a hypervisor is already running (WSL2 / Hyper-V already on)
$virt = [bool]($cpu.VirtualizationFirmwareEnabled -or $hyper.HypervisorPresent)
Add-Check "virtualization" $virt "error" $(if ($hyper.HypervisorPresent) { "hypervisor running" } else { [string]$cpu.VirtualizationFirmwareEnabled })
$wsl = $false
try { & wsl.exe --status *> $null; $wsl = ($LASTEXITCODE -eq 0) } catch { }
Add-Check "wsl" $wsl "info" $wsl
$distro = $false
try { $distro = [bool](((& wsl.exe -l -q 2>$null) -replace "`0", "") | Where-Object { $_.Trim() -eq "PharmacyOS" }) } catch { }
Add-Check "existing_server" (-not $distro) "info" $distro
$port80 = [bool](Get-NetTCPConnection -State Listen -LocalPort 80)
Add-Check "port80" (-not $port80) "info" $port80
$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = ([Security.Principal.WindowsPrincipal]$me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
# a member of Administrators, elevated or not (not elevated, the group is in the token as "deny only")
$inAdmins = $admin -or [bool]($me.Claims | Where-Object { $_.Value -eq "S-1-5-32-544" }) -or [bool](whoami /groups | Select-String "S-1-5-32-544")
# blocking: the server environment and its tasks belong to the Windows account that runs the setup. A
# standard user elevating with someone else's administrator password would install them for that other
# account, where this user's PharmacyOS can never reach them.
Add-Check "administrator" $inAdmins "error" $admin
$blocking = @($checks | Where-Object { $_.level -eq "error" -and -not $_.ok })
[ordered]@{ ok = ($blocking.Count -eq 0); checks = $checks; build = $build } | ConvertTo-Json -Depth 4 -Compress
