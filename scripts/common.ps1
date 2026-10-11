Set-StrictMode -Version Latest

function Get-TdxTools([string]$CMakePath = '', [string]$Generator = '') {
    $vsInstall = ''
    $vsVersion = ''
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $vsInstall = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $vsVersion = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationVersion
    }
    if ($CMakePath) {
        $cmake = (Resolve-Path -LiteralPath $CMakePath).Path
    } else {
        $bundled = if ($vsInstall) { Join-Path $vsInstall 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe' } else { '' }
        $cmake = if ($bundled -and (Test-Path -LiteralPath $bundled)) { $bundled } else { (Get-Command cmake.exe -ErrorAction Stop).Source }
    }
    if (-not $Generator) {
        $Generator = if ($vsVersion -match '^18\.') { 'Visual Studio 18 2026' } else { 'Visual Studio 17 2022' }
    }
    $available = & $cmake --help
    if ($LASTEXITCODE -ne 0 -or -not ($available -match [regex]::Escape($Generator))) {
        throw "CMake does not support generator: $Generator. Specify -CMakePath and -Generator."
    }
    return @{ CMake = $cmake; CTest = (Join-Path (Split-Path -Parent $cmake) 'ctest.exe'); Generator = $Generator }
}

function Get-TdxBuildRoot([string]$ProjectRoot, [string]$BuildRoot) {
    if (-not $BuildRoot) { return (Join-Path $ProjectRoot 'build') }
    if ([System.IO.Path]::IsPathRooted($BuildRoot)) { return [System.IO.Path]::GetFullPath($BuildRoot) }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $BuildRoot))
}

function Invoke-TdxPython([string]$PythonExecutable, [string[]]$Arguments) {
    if ($PythonExecutable) {
        & $PythonExecutable -B @Arguments
    } elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3.12 -B @Arguments
    } else {
        & python.exe -B @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python command failed.' }
}
