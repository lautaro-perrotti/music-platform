# Remove Copilot-owned runtime assets only. Never user projects or Ableton prefs.
[CmdletBinding()]
param(
    [switch]$RemoveVenv,
    [switch]$RemoveConfig
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $args = @("-m", "copilot.cli", "uninstall-copilot")
    if ($RemoveVenv) { $args += "--remove-venv" }
    if ($RemoveConfig) { $args += "--remove-config" }
    & $venvPython @args
    $code = $LASTEXITCODE
} else {
    Write-Host "No .venv Python. Skipping Python-owned uninstall; filesystem cleanup is limited."
    $code = 0
}

if ($RemoveVenv -and (Test-Path (Join-Path $RepoRoot ".venv"))) {
    Remove-Item -Recurse -Force (Join-Path $RepoRoot ".venv")
    Write-Host "Removed .venv"
}

Write-Host "Uninstall does not remove user projects, Ableton preferences, or unmanaged User Library files."
exit $code
