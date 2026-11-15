# Clones AgOpenWeb at the commit this project was built against and applies our small changes
# (patches/agopenweb-fleet.patch: per-instance port, per-port single-instance lock, fleet-bridge.js).
# The fleet bridge (fleet/bridge) builds against this clone, so run this once after cloning the repo.
# Needs git and the .NET 10 SDK (winget install Microsoft.DotNet.SDK.10).
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$repo = Join-Path $root 'AgOpenWeb'
$commit = '118a075e60b39a7ea3d8a78aebcac8703a1c4d21'

if (-not (Test-Path $repo)) {
    git clone https://github.com/AgOpenGPS-Official/AgOpenWeb.git $repo
    if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
}
Push-Location $repo
try {
    git checkout -q $commit
    if ($LASTEXITCODE -ne 0) { throw "Could not check out $commit." }
    git apply --check (Join-Path $root 'patches\agopenweb-fleet.patch') 2>$null
    if ($LASTEXITCODE -eq 0) {
        git apply (Join-Path $root 'patches\agopenweb-fleet.patch')
        Write-Host 'Applied patches/agopenweb-fleet.patch'
    } else {
        Write-Host 'Patch already applied (or the tree has other changes); left as is.'
    }
} finally { Pop-Location }
Write-Host 'AgOpenWeb ready. Next: double-click "Start Fleet Simulation.bat".'
