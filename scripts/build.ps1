param(
    [string]$Python = (Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'),
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if (-not $SkipInstall) {
        & $Python -m pip install '.[build]'
        if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
    }
    # Keep PyInstaller's cache inside the project, including on restricted build hosts.
    $previousCache = $env:PYINSTALLER_CONFIG_DIR
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot 'build\pyinstaller-cache'
    try {
        & $Python -m PyInstaller --noconfirm --clean fullremote-mcp.spec
        if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
    }
    finally {
        $env:PYINSTALLER_CONFIG_DIR = $previousCache
    }
    Write-Host 'Built dist\fullremote-mcp.exe'
}
finally {
    Pop-Location
}
