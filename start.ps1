param([switch]$Mobile, [ValidateRange(1024, 65535)][int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskServerArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'server.py'), '--port', "$Port")
if (-not $NoBrowser) { $taskServerArgs += '--open' }
if ($Mobile) {
    $taskLanAddress = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } | ForEach-Object { $_.IPv4Address.IPAddress } | Where-Object { $_ -notlike '169.254.*' -and $_ -ne '127.0.0.1' } | Select-Object -First 1
    if (-not $taskLanAddress) { throw 'No active LAN IPv4 address. Connect this computer to Wi-Fi first.' }
    $taskServerArgs += @('--host', '0.0.0.0', '--allow-host', $taskLanAddress)
    Write-Host "Phone preview (same Wi-Fi): http://${taskLanAddress}:${Port}"
}
$taskBundledPython = Join-Path $PSScriptRoot 'runtime\python.exe'
$taskPython = Get-Command python -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $taskBundledPython) {
    & $taskBundledPython @taskServerArgs
} elseif ($taskPython) {
    & $taskPython.Source @taskServerArgs
} else {
    py -3 @taskServerArgs
}
exit $LASTEXITCODE

