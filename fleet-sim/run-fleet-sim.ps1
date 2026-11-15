# Starts the three-tractor simulation and opens the dashboard.
#
#   fleet bridge (C#)      3 x AgOpenWeb VirtualModuleHub, $PANDA GPS on UDP 10001 / 10002 / 10003
#   coordinator (Python)   listens to that GPS, plans and commands the tractors, serves the dashboard
#   dashboard              http://localhost:8080
#
# Usage:  .\run-fleet-sim.ps1            start (builds the bridge the first time)
#         .\run-fleet-sim.ps1 -Rebuild   rebuild the bridge, then start
#         .\run-fleet-sim.ps1 -Stop      stop both programs
param([switch]$Stop, [switch]$Rebuild, [switch]$NoBrowser)

$fleet = Join-Path $PSScriptRoot 'fleet'
$logs  = Join-Path $fleet 'logs'
$exe   = Join-Path $fleet 'bridge\bin\Release\net10.0\FleetBridge.exe'
$pids  = Join-Path $logs 'pids.txt'
$env:Path = "C:\Program Files\dotnet;" + $env:Path
New-Item -ItemType Directory -Force $logs | Out-Null

# Stop whatever an earlier run left behind.
if (Test-Path $pids) {
    (Get-Content $pids) -split ',' | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Remove-Item $pids
}
Get-Process FleetBridge -ErrorAction SilentlyContinue | Stop-Process -Force
if ($Stop) { Write-Host 'Fleet simulation stopped.'; return }

if ($Rebuild -or -not (Test-Path $exe)) {
    dotnet build (Join-Path $fleet 'bridge\FleetBridge.csproj') -c Release -v q -nologo
    if ($LASTEXITCODE -ne 0) { throw 'Bridge build failed.' }
}

$bridge = Start-Process $exe -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs 'bridge.log') -RedirectStandardError (Join-Path $logs 'bridge.err.log')
$coord = Start-Process python -ArgumentList '-u', ('"' + (Join-Path $fleet 'coordinator.py') + '"') -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs 'coordinator.log') -RedirectStandardError (Join-Path $logs 'coordinator.err.log')
"$($bridge.Id),$($coord.Id)" | Set-Content $pids

Write-Host "Bridge      pid $($bridge.Id)   GPS on UDP 10001-10003, commands on UDP 10100"
Write-Host "Coordinator pid $($coord.Id)   http://localhost:8080"
Write-Host "Logs: $logs"
if (-not $NoBrowser) { Start-Sleep 2; Start-Process 'http://localhost:8080' }
