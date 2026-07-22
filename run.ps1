$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$ProjectRoot\literature_rss.py" --config "$ProjectRoot\config.json" --output "$ProjectRoot\public"
if ($LASTEXITCODE -ne 0) {
    throw "Literature RSS update failed with exit code $LASTEXITCODE"
}
Write-Host "RSS updated: $ProjectRoot\public\feed.xml"
