$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PublicRoot = Join-Path $ProjectRoot 'public'
if (-not (Test-Path (Join-Path $PublicRoot 'feed.xml'))) {
    & (Join-Path $ProjectRoot 'run.ps1')
}
Set-Location $PublicRoot
Write-Host 'Local RSS URL: http://localhost:8000/feed.xml'
python -m http.server 8000
