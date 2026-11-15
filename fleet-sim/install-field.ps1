# Copies a field.geojson exported from fleet-dashboard.html into all three tractors' Fields folders,
# so every AgOpenWeb instance can open the same field.
# Usage:  .\install-field.ps1                       (newest field*.geojson in Downloads, named FleetField)
#         .\install-field.ps1 -File C:\path\field.geojson -Name NorthField
param([string]$File, [string]$Name = 'FleetField')

if (-not $File) {
    $f = Get-ChildItem "$env:USERPROFILE\Downloads" -Filter 'field*.geojson' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $f) { throw 'No field*.geojson in Downloads. Export one from the dashboard first, or pass -File.' }
    $File = $f.FullName
}
$gj = Get-Content $File -Raw | ConvertFrom-Json
$meta = $gj.features | Where-Object { $_.properties.role -eq 'metadata' } | Select-Object -First 1
if (-not $meta) { throw "$File has no metadata feature; it is not an AgOpenWeb field file." }
$inv = [Globalization.CultureInfo]::InvariantCulture
$origin = ([double]$meta.properties.originLatitude).ToString('F8', $inv) + ',' + ([double]$meta.properties.originLongitude).ToString('F8', $inv)
$json = (Get-Content $File -Raw) -replace '"name":\s*"[^"]*"', ('"name": "' + $Name + '"')
$utf8 = New-Object Text.UTF8Encoding($false)

foreach ($t in 'tractor1', 'tractor2', 'tractor3') {
    $dir = Join-Path $PSScriptRoot "fleet-data\$t\AgOpenWeb\Fields\$Name"
    New-Item -ItemType Directory -Force $dir | Out-Null
    [IO.File]::WriteAllText((Join-Path $dir 'field.geojson'), $json, $utf8)
    [IO.File]::WriteAllText((Join-Path $dir 'field.origin'), $origin, $utf8)
    Write-Host "$t <- $dir"
}

# fleet-field.js tells fleet-view.html which field to open and where it is.
$outer = $gj.features | Where-Object { $_.properties.role -eq 'outer-boundary' } | Select-Object -First 1
$ring = @($outer.geometry.coordinates[0] | ForEach-Object { '[' + ([double]$_[1]).ToString('F8', $inv) + ',' + ([double]$_[0]).ToString('F8', $inv) + ']' }) -join ','
$js = "window.FLEET_FIELD = { name: '$Name', origin: [$origin], ring: [$ring] };"
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'fleet-field.js'), $js, $utf8)

Write-Host "Field '$Name' (origin $origin) installed from $File"
Write-Host "Restart the tractors (.\start-fleet.ps1) so they list the new field, then reload fleet-view.html."
