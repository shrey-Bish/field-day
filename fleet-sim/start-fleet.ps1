# Starts three AgOpenWeb instances (one per tractor), each headless with its own port and data folder.
#   Tractor 1 -> http://localhost:5174
#   Tractor 2 -> http://localhost:5175
#   Tractor 3 -> http://localhost:5176
# Usage:  .\start-fleet.ps1            (build if needed, then start)
#         .\start-fleet.ps1 -Stop      (stop all three)
param([switch]$Stop, [switch]$Rebuild)

$root = $PSScriptRoot
$repo = Join-Path $root 'AgOpenWeb'
$exe  = Join-Path $repo 'Platforms\AgOpenWeb.Desktop\bin\Debug\net10.0\AgOpenWeb.Desktop.exe'
$env:Path = "C:\Program Files\dotnet;" + $env:Path

Get-Process AgOpenWeb.Desktop -ErrorAction SilentlyContinue | Stop-Process -Force
if ($Stop) { Write-Host 'Fleet stopped.'; return }

if ($Rebuild -or -not (Test-Path $exe)) {
    dotnet build (Join-Path $repo 'Platforms\AgOpenWeb.Desktop\AgOpenWeb.Desktop.csproj') -c Debug -v q -nologo
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
}

$tractors = @(
    @{ Id = 'tractor1'; Port = 5174 },
    @{ Id = 'tractor2'; Port = 5175 },
    @{ Id = 'tractor3'; Port = 5176 }
)

foreach ($t in $tractors) {
    $data = Join-Path $root "fleet-data\$($t.Id)"
    New-Item -ItemType Directory -Force $data | Out-Null
    $env:AGOPENWEB_PORT = "$($t.Port)"
    $env:AGOPENWEB_DATA = $data
    Start-Process -FilePath $exe -ArgumentList '--headless' -WorkingDirectory (Split-Path $exe) `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $data 'host.log') `
        -RedirectStandardError  (Join-Path $data 'host.err.log')
    Write-Host "$($t.Id) -> http://localhost:$($t.Port)   (data: $data)"
}
Remove-Item Env:AGOPENWEB_PORT, Env:AGOPENWEB_DATA
