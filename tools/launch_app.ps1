<#
.SYNOPSIS
Launch the Tomography Session Browser against the working tree in this
repository, not the editable-installed copy at Tomography_app_codex.

.DESCRIPTION
PowerShell sibling of launch_app.bat. Sets PYTHONPATH so the working-tree
package wins, optionally enables the developer perf report, and runs the
app via the tomoapp_claude conda env's Python.

.PARAMETER Perf
When supplied, sets TOMOAPP_PERF=1 so the perf report is printed to
stderr after each session load.

.EXAMPLE
.\tools\launch_app.ps1
.\tools\launch_app.ps1 -Perf
#>

[CmdletBinding()]
param(
    [switch]$Perf
)

$ErrorActionPreference = "Stop"

# Working-tree root is one level above this script.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Prepend repo root so its package shadows the editable install.
$env:PYTHONPATH = if ($env:PYTHONPATH) {
    "$repoRoot;$env:PYTHONPATH"
} else {
    $repoRoot
}

if ($Perf) {
    $env:TOMOAPP_PERF = "1"
}

$condaPy = Join-Path $env:USERPROFILE "miniconda3\envs\tomoapp_claude\python.exe"
if (-not (Test-Path $condaPy)) {
    Write-Error "Could not find tomoapp_claude python at $condaPy. Edit launch_app.ps1 or 'conda activate tomoapp_claude' first."
    exit 1
}

& $condaPy -m tomography_session_browser.main @args
