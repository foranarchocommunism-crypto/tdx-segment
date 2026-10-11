[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ValidationReport,
    [string]$BuildRoot = '',
    [string]$Output = '',
    [string]$PythonExecutable = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildBase = Get-TdxBuildRoot $projectRoot $BuildRoot
$options = @((Join-Path $PSScriptRoot 'package.py'), '--build-root', $buildBase, '--validation-report', (Resolve-Path -LiteralPath $ValidationReport).Path)
if ($Output) { $options += @('--output', [System.IO.Path]::GetFullPath($Output)) }
Invoke-TdxPython $PythonExecutable $options
