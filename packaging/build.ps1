param(
    [string]$Python = 'python',
    [string]$BuildId = ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
)
. (Join-Path $PSScriptRoot 'Set-BuildEnvironment.ps1')
if ($BuildId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
    throw 'BuildId must be a single plain directory name (up to 64 characters).'
}
$distRoot = Join-Path $projectRoot "dist/portable/$BuildId"
if (Test-Path -LiteralPath $distRoot) {
    throw "Build destination already exists; choose a new BuildId to preserve existing app data: $distRoot"
}
$buildPython = Join-Path $stateRoot 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    & $Python -B -m venv --copies (Join-Path $stateRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create project build environment' }
}
$sdkWheel = Join-Path $stateRoot 'vendor/FileSDK-2026.1.2-cp313-cp313-win_amd64.whl'
if (-not (Test-Path -LiteralPath $sdkWheel)) {
    throw 'Place the locally licensed cp313 SDK wheel under .packaging/vendor first; no system SDK will be installed.'
}
& $buildPython -B -m pip install -r (Join-Path $PSScriptRoot 'requirements-build.lock') $sdkWheel
if ($LASTEXITCODE -ne 0) { throw 'Project-only dependency installation failed' }
$archive = Join-Path $stateRoot 'vendor/ffmpeg-9.0.2-essentials_build.zip'
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest -Uri 'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip' -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '60F467265B1E312373DBCD92200C2618A74850F98D3D078E94296BB3FA2047BA') {
    throw 'FFmpeg checksum mismatch'
}
& $buildPython -B -m PyInstaller --noconfirm --distpath $distRoot --workpath (Join-Path $projectRoot "build/portable/$BuildId") (Join-Path $PSScriptRoot 'flir_seq_tool.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
& $buildPython -B (Join-Path $PSScriptRoot 'prepare_bundle.py') --app (Join-Path $distRoot 'FLIR_SEQ_Tool')
if ($LASTEXITCODE -ne 0) { throw 'Bundle manifest generation failed' }
& $buildPython -B (Join-Path $PSScriptRoot 'package_bundle.py') --app (Join-Path $distRoot 'FLIR_SEQ_Tool')
if ($LASTEXITCODE -ne 0) { throw 'Bundle ZIP verification failed' }
