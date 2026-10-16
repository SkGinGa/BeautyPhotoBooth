param([string]$InnoCompiler = '',[string]$CertificateThumbprint = '',[string]$SignToolPath = '')
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskVenv = Join-Path $taskRoot 'build-work\venv'
if (-not (Test-Path -LiteralPath (Join-Path $taskVenv 'Scripts\python.exe'))) {
    & py -3.12 -m venv $taskVenv
    if ($LASTEXITCODE -ne 0) { throw 'Install 64-bit Python 3.12 first.' }
}
$taskPython = Join-Path $taskVenv 'Scripts\python.exe'
& $taskPython -m pip install -r (Join-Path $taskRoot 'source\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $taskPython (Join-Path $taskRoot 'tools\build.py')
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
if ($CertificateThumbprint) {
    & (Join-Path $taskRoot 'tools\Sign.ps1') -FilePath (Join-Path $taskRoot 'build-work\dist\PhotoBooth\PhotoBooth.exe') -CertificateThumbprint $CertificateThumbprint -SignToolPath $SignToolPath
}
if ($InnoCompiler) {
    & $InnoCompiler (Join-Path $taskRoot 'installer\PhotoBooth.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    if ($CertificateThumbprint) {
        & (Join-Path $taskRoot 'tools\Sign.ps1') -FilePath (Join-Path $taskRoot 'releases\BeautyPhotoBooth-1.1.0-Setup.exe') -CertificateThumbprint $CertificateThumbprint -SignToolPath $SignToolPath
    }
}
if (-not $CertificateThumbprint) { Write-Output 'Unsigned build: author information is embedded; no code-signing certificate was supplied.' }
