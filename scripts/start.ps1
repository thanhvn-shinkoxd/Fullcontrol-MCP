param(
    [ValidateSet('http', 'stdio')]
    [string]$Transport = 'http',
    [string]$Certificate,
    [string]$PrivateKey
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Run .\scripts\setup.ps1 first.'
}
$serverArgs = @('-m', 'fullremote_mcp', 'serve', '--transport', $Transport)
if ($Certificate) { $serverArgs += @('--ssl-certfile', $Certificate) }
if ($PrivateKey) { $serverArgs += @('--ssl-keyfile', $PrivateKey) }
Push-Location -LiteralPath $projectRoot
try {
    & $venvPython @serverArgs
    $serverExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $serverExitCode
