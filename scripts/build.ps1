[CmdletBinding()]
param(
    [ValidateSet('x86', 'x64', 'all')][string]$Architecture = 'all',
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Release',
    [string]$BuildRoot = '',
    [string]$CMakePath = '',
    [string]$Generator = '',
    [string]$VendorIncludeDir = '',
    [string]$PythonExecutable = '',
    [switch]$DllOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$projectRoot = Split-Path -Parent $PSScriptRoot
$tools = Get-TdxTools $CMakePath $Generator
$buildBase = Get-TdxBuildRoot $projectRoot $BuildRoot
$architectures = if ($Architecture -eq 'all') { @('x86', 'x64') } else { @($Architecture) }
foreach ($arch in $architectures) {
    $platform = if ($arch -eq 'x86') { 'Win32' } else { 'x64' }
    $directory = Join-Path $buildBase $arch
    $cache = Join-Path $directory 'CMakeCache.txt'
    if (Test-Path -LiteralPath $cache) {
        $cachedRoot = Select-String -LiteralPath $cache -Pattern '^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$'
        if ($cachedRoot -and $cachedRoot.Matches[0].Groups[1].Value -ne $projectRoot.Replace('\', '/')) {
            throw "Build cache belongs to another source directory: $directory"
        }
    }
    $testing = if ($DllOnly) { 'OFF' } else { 'ON' }
    $options = @('-S', $projectRoot, '-B', $directory, '-G', $tools.Generator, '-A', $platform, "-DBUILD_TESTING=$testing")
    if ($VendorIncludeDir) { $options += "-DTDX_VENDOR_INCLUDE_DIR=$((Resolve-Path -LiteralPath $VendorIncludeDir).Path)" }
    if ($PythonExecutable -and -not $DllOnly) { $options += "-DPython3_EXECUTABLE=$PythonExecutable" }
    & $tools.CMake @options
    if ($LASTEXITCODE -ne 0) { throw "Configure failed: $arch" }
    & $tools.CMake --build $directory --config $Configuration --parallel 4
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $arch" }
}
Write-Host "Build complete: $buildBase"
