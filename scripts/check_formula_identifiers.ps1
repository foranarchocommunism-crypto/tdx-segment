[CmdletBinding()]
param(
    [string[]]$Path = @()
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot

if ($Path.Count -eq 0) {
    $Path = @(
        Get-ChildItem -LiteralPath (Join-Path $projectRoot 'examples/tdx') `
            -Filter '*.txt' -File |
            Select-Object -ExpandProperty FullName
    )

}

$violations = foreach ($formulaPath in $Path) {
    $resolvedPath = (Resolve-Path -LiteralPath $formulaPath).Path
    $text = Get-Content -LiteralPath $resolvedPath -Raw

    # 保持字符位置不变地遮蔽大括号注释和单引号字符串，避免把提示文字
    # 当成公式标识符。通达信对实际单词执行 15 字符上限。
    $code = [regex]::Replace(
        $text,
        '\{[\s\S]*?\}',
        { param($match) ' ' * $match.Length }
    )
    $code = [regex]::Replace(
        $code,
        "'[^']*'",
        { param($match) ' ' * $match.Length }
    )

    foreach ($match in [regex]::Matches($code, '[A-Za-z_][A-Za-z0-9_]*')) {
        if ($match.Length -le 15) {
            continue
        }
        $prefix = $text.Substring(0, $match.Index)
        $line = [regex]::Matches($prefix, "`n").Count + 1
        $lastLineFeed = $prefix.LastIndexOf("`n")
        [pscustomobject]@{
            Path = $resolvedPath
            Line = $line
            Column = $match.Index - $lastLineFeed
            Word = $match.Value
            Length = $match.Length
        }
    }
}

if (@($violations).Count -gt 0) {
    $violations |
        Sort-Object Path, Line, Column |
        Format-Table Path, Line, Column, Word, Length -AutoSize
    Write-Host '公式标识符检查失败：单词长度不得超过 15 个字符。'
    exit 1
}

Write-Host '公式标识符检查通过。'
exit 0
