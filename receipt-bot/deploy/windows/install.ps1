# receipt-bot ni Windows serverga o'rnatish.
# Ishga tushirish (Administrator PowerShell, receipt-bot papkasida):
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

$python = (Get-Command py -ErrorAction SilentlyContinue)
if (-not $python) { throw "Python topilmadi. python.org dan Python 3.12 ni o'rnating ('Add python.exe to PATH' belgilansin)." }

if (-not (Test-Path ".venv")) {
    Write-Host "Virtual muhit yaratilmoqda..."
    py -3 -m venv .venv
}
Write-Host "Paketlar o'rnatilmoqda..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -q

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env yaratildi - uni Notepad bilan ochib BOT_TOKEN, RELAY_URL va RELAY_KEY ni to'ldiring."
}
Write-Host "Tayyor. Sinab ko'rish: deploy\windows\run.bat"
