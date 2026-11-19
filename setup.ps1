# Windows PowerShell 5.1+ bootstrap. Works with py.exe or a standard Python install.
param([switch]$Check, [switch]$Yes)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Test-Python([string[]]$exe) {
    try {
        $argsList = @()
        if ($exe.Length -gt 1) { $argsList = @($exe[1..($exe.Length - 1)]) }
        $argsList += @('-c', 'import sys,venv,ssl,sqlite3,ensurepip; assert sys.version_info >= (3,10)')
        & $exe[0] @argsList *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    $candidates = New-Object System.Collections.ArrayList
    if ($env:TOHRU_DB_PYTHON) { [void]$candidates.Add(@($env:TOHRU_DB_PYTHON)) }
    # Prefer the explicit Python on PATH (e.g. a selected x64 runtime on Windows ARM64)
    # before the system py launcher, which may default to native ARM64 Python.
    foreach ($name in @('python','python3','py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($name -eq 'py') {
            foreach ($version in @('-3.14','-3.13','-3.12','-3.11','-3.10','-3')) {
                [void]$candidates.Add(@($cmd.Source, $version))
            }
        } else { [void]$candidates.Add(@($cmd.Source)) }
    }
    foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python")) {
        if (-not (Test-Path -LiteralPath $base)) { continue }
        Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object {
            $candidate = Join-Path $_.FullName 'python.exe'
            if (Test-Path -LiteralPath $candidate) { [void]$candidates.Add(@($candidate)) }
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Python $candidate) { return ,$candidate }
    }
    return $null
}

$python = Find-Python
if ($Check) {
    if (-not $python) { Write-Error '[DB Context] Missing Python 3.10+ with venv/ensurepip/ssl/sqlite3.'; exit 1 }
    $arguments = @()
    if ($python.Length -gt 1) { $arguments += $python[1..($python.Length - 1)] }
    $arguments += @('scripts/setup.py', '--check')
    & $python[0] @arguments
    exit $LASTEXITCODE
}
if (-not $python) {
    Write-Host '[DB Context] Missing a compatible Python 3.10+ installation.'
    Write-Host '[DB Context] Python will not be installed without approval.'
    $approved = $Yes -or ((Read-Host 'Install Python using Windows Package Manager (winget)? [y/N]') -match '^(y|yes)$')
    if (-not $approved) { Write-Error 'Setup stopped. Install Python and retry.'; exit 1 }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Error 'winget is not installed. Install Python 3.10+ from https://www.python.org/downloads/windows/ and retry.'
        exit 1
    }
    & winget install --exact --id Python.Python.3.13 --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Write-Error 'winget could not install a compatible Python for this device. Install Python manually and retry.'; exit 1 }
    $python = Find-Python
    if (-not $python) {
        Write-Error 'Python was installed but is not visible in this PowerShell session. Open a new terminal and rerun setup.'
        exit 1
    }
}
$arguments = @()
if ($python.Length -gt 1) { $arguments += $python[1..($python.Length - 1)] }
$arguments += @('scripts/setup.py')
if ($Yes) { $arguments += '--yes' }
& $python[0] @arguments
exit $LASTEXITCODE
