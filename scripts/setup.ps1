param(
    [string]$Python = 'python',
    [switch]$Dev
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $package = if ($Dev) { '.[dev]' } else { '.' }
    & $venvPython -m pip install -e $package
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check package index connectivity.' }
    $envFile = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile)) {
        $token = & $venvPython -m fullremote_mcp token
        if ($LASTEXITCODE -ne 0) { throw 'Could not generate the server token.' }
        $template = Get-Content -LiteralPath (Join-Path $projectRoot '.env.example') -Raw
        $template.Replace('FULLREMOTE_TOKEN=', ('FULLREMOTE_TOKEN=' + $token.Trim())) |
            Set-Content -LiteralPath $envFile -Encoding ASCII
        Write-Host 'Created .env with a generated bearer token.'
    }
    & $venvPython -m fullremote_mcp doctor
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostics failed.' }
    Write-Host 'Setup complete. Start the server with .\scripts\start.ps1'
}
finally {
    Pop-Location
}
