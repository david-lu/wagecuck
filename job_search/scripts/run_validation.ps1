[CmdletBinding()]
param(
    [string]$Profile = "profiles/los-angeles-or-remote.json"
)

$ErrorActionPreference = "Stop"
$JobSearchRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $JobSearchRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $PSScriptRoot "find_three_titles.py"
$ProfilePath = if ([System.IO.Path]::IsPathRooted($Profile)) {
    $Profile
} else {
    Join-Path $JobSearchRoot $Profile
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Job-search virtual environment not found: $Python"
}
if (-not (Test-Path -LiteralPath $ProfilePath)) {
    throw "Search profile not found: $ProfilePath"
}

$LogDirectory = Join-Path $JobSearchRoot ".artifacts\console"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDirectory "validation-$Timestamp.log"

Write-Host "Search profile: $ProfilePath"
Write-Host "Console log: $LogPath"
Write-Host "Validating the existing 02-filter.csv; validation progress appears below."
Write-Host "Ctrl+C safely stops validation; rerun this command to resume from the journal."

Push-Location $JobSearchRoot
try {
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python $Runner --profile $ProfilePath --validation-only 2>&1 |
        Tee-Object -FilePath $LogPath
    $ExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
    Pop-Location
}

exit $ExitCode
