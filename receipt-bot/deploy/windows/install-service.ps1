# receipt-bot ni Windows ishga tushganda avtomatik yoqiladigan qilish (Task Scheduler, SYSTEM nomidan).
# Xato bilan to'xtasa har daqiqada qayta ishga tushiriladi.
# Ishga tushirish (Administrator PowerShell, receipt-bot papkasida):
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Avval install.ps1 ni ishga tushiring." }
if (-not (Test-Path (Join-Path $root ".env"))) { throw ".env fayli yo'q." }

$name = "receipt-bot"
$log = Join-Path $root "data\service.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"set PYTHONUTF8=1&& `"$python`" -m app.main >> `"$log`" 2>&1`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 9999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Xizmat o'rnatildi va ishga tushdi. Log: $log"
Write-Host "Holat: Get-ScheduledTask receipt-bot | Select State   To'xtatish: Stop-ScheduledTask receipt-bot"
