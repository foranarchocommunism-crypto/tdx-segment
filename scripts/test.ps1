[CmdletBinding()]
param(
    [ValidateSet('x86', 'x64', 'all')][string]$Architecture = 'all',
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Release',
    [string]$BuildRoot = '',
    [string]$CMakePath = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$projectRoot = Split-Path -Parent $PSScriptRoot
$tools = Get-TdxTools $CMakePath
$buildBase = Get-TdxBuildRoot $projectRoot $BuildRoot
$architectures = if ($Architecture -eq 'all') { @('x86', 'x64') } else { @($Architecture) }
foreach ($arch in $architectures) {
    $directory = Join-Path $buildBase $arch
    if (-not (Test-Path -LiteralPath (Join-Path $directory 'CTestTestfile.cmake'))) { throw "Configure with BUILD_TESTING=ON first: $directory" }
    & $tools.CTest --test-dir $directory -C $Configuration --parallel 1 --output-on-failure --output-junit (Join-Path $directory 'ctest-results.xml')
    if ($LASTEXITCODE -ne 0) { throw "CTest failed: $arch" }
}
