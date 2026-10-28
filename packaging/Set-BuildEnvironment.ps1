$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $projectRoot
$stateRoot = Join-Path $projectRoot '.packaging'
foreach ($relative in @('tmp', 'cache/pip', 'cache/matplotlib', 'cache/pyinstaller', 'cache/pycache')) {
    New-Item -ItemType Directory -Path (Join-Path $stateRoot $relative) -Force | Out-Null
}
# These settings apply only to this PowerShell process and its children.
$env:TEMP = Join-Path $stateRoot 'tmp'
$env:TMP = $env:TEMP
$env:PYTEST_DEBUG_TEMPROOT = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $stateRoot 'cache/pip'
$env:MPLCONFIGDIR = Join-Path $stateRoot 'cache/matplotlib'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $stateRoot 'cache/pyinstaller'
$env:XDG_CACHE_HOME = Join-Path $stateRoot 'cache'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONPATH = Join-Path $projectRoot 'src'
