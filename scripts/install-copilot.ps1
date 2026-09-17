# SECOND_MACHINE_INSTALLER_V1 — Windows bootstrap. Canonical DAW/M4L logic is Python.
[CmdletBinding()]
param(
    [switch]$SkipWinget
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Write-JsonFile {
    param($Path, $Object)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
    $Object | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Get-PythonVersionParts {
    param([string]$Exe)
    $raw = & $Exe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        return $null
    }
    return $raw.Trim()
}

function Test-PythonCompatible {
    param([string]$Exe)
    & $Exe -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)" | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Find-CompatiblePython {
    $candidates = @()
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidates += "py -3.12"
        $candidates += "py -3"
    }
    foreach ($name in @("python3.12", "python312", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $candidates += $cmd.Source
        }
    }
    foreach ($item in $candidates) {
        try {
            if ($item -like "py *") {
                $parts = $item.Split(" ")
                & $parts[0] $parts[1] -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)" | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    return @{ Exe = $parts[0]; Args = @($parts[1]); Display = $item }
                }
            } else {
                if (Test-PythonCompatible -Exe $item) {
                    return @{ Exe = $item; Args = @(); Display = $item }
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Invoke-Python {
    param($Spec, [string[]]$PythonArgs)
    if ($Spec.Args.Count -gt 0) {
        & $Spec.Exe @($Spec.Args) @PythonArgs
    } else {
        & $Spec.Exe @PythonArgs
    }
}

Write-Host "SECOND_MACHINE_INSTALLER_V1"
Write-Host "repository: $RepoRoot"

$discovery = [ordered]@{
    windows_version = [System.Environment]::OSVersion.VersionString
    architecture = $env:PROCESSOR_ARCHITECTURE
    repository_path = $RepoRoot
    documents_path = [Environment]::GetFolderPath("MyDocuments")
    skip_winget = [bool]$SkipWinget
}
Write-JsonFile -Path (Join-Path $RepoRoot "logs\windows_bootstrap_discovery.json") -Object $discovery

$pythonStatus = "MISSING"
$wingetUsed = $false
$pythonSpec = Find-CompatiblePython

if (-not $pythonSpec -and -not $SkipWinget) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Compatible Python not found. Installing Python.Python.3.12 via winget."
        & winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
        $wingetUsed = $true
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        $pythonSpec = Find-CompatiblePython
    }
}

if (-not $pythonSpec) {
    $required = [ordered]@{
        status = "PYTHON_INSTALL_REQUIRED"
        exact_command = "winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements"
        detail = "Python 3.12+ was not found and could not be installed safely. Install 3.12, reopen the shell, rerun this script."
    }
    Write-JsonFile -Path (Join-Path $RepoRoot "logs\second_machine_installer_v1.json") -Object $required
    $required | ConvertTo-Json -Depth 6
    exit 2
}

$pythonStatus = "ALREADY_CURRENT"
if ($wingetUsed) {
    $pythonStatus = "INSTALLED"
}

$venvDir = Join-Path $RepoRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvStatus = "ALREADY_CURRENT"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment at .venv"
    Invoke-Python -Spec $pythonSpec -PythonArgs @("-m", "venv", $venvDir)
    if (-not (Test-Path $venvPython)) {
        Write-Error "Failed to create $venvPython"
        exit 2
    }
    $venvStatus = "CREATED"
} elseif (-not (Test-PythonCompatible -Exe $venvPython)) {
    Write-Error ".venv exists but is not Python 3.12+. Delete .venv and rerun."
    exit 2
}

Write-Host "Installing project into .venv (declared pyproject dependencies)."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit 2 }
& $venvPython -m pip install -e $RepoRoot
if ($LASTEXITCODE -ne 0) { exit 2 }

& $venvPython -c "import copilot, pydantic, numpy, soundfile"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Import verification failed."
    exit 2
}

$env:COPILOT_INSTALLER_PYTHON_STATUS = $pythonStatus
$env:COPILOT_INSTALLER_VENV_STATUS = $venvStatus
$env:COPILOT_INSTALLER_DEPS_STATUS = "ALREADY_CURRENT"
if ($wingetUsed) {
    $env:COPILOT_INSTALLER_WINGET_USED = "1"
}

Write-Host "Installing Remote Script, M4L runtime, and config via Python."
& $venvPython -m copilot.cli install
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Use this interpreter:"
Write-Host "  $venvPython -m copilot.cli doctor"
Write-Host "Or: .\.venv\Scripts\Activate.ps1"
exit $code
