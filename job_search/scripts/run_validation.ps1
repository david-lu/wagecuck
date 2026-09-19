[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputCsv,
    [Parameter(Mandatory = $true)][string]$OutputCsv,
    [string]$Fields,
    [string]$Cache
)
$ErrorActionPreference = "Stop"
$JobSearchRoot = Split-Path -Parent $PSScriptRoot
$JobSearchPython = Join-Path $JobSearchRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $JobSearchPython)) {
    throw "Job-search virtual environment not found: $JobSearchPython"
}
$CommandArguments = @("-m", "wagecuck_search", "validate", $InputCsv, "--output", $OutputCsv)
if ($Fields) { $CommandArguments += @("--fields", $Fields) }
if ($Cache) { $CommandArguments += @("--cache", $Cache) }
& $JobSearchPython @CommandArguments
exit $LASTEXITCODE
