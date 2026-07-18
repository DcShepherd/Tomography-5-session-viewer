@echo off
REM Launch the Tomography Session Browser using the *working tree* in this
REM repository (Claude_tomoapp optimisation), not the editable-installed
REM copy at Tomography_app_codex. Run this from anywhere — the script
REM resolves paths relative to its own location.
REM
REM Usage:
REM   tools\launch_app.bat              -- launch with normal logging
REM   tools\launch_app.bat --perf       -- launch with TOMOAPP_PERF=1 (perf
REM                                        report on stderr after each load)
REM
REM If you want the .bat to be reusable from a desktop shortcut, point the
REM shortcut at this file and leave "Start in" blank — %~dp0 resolves to
REM the tools\ directory regardless of CWD.

setlocal

REM The working-tree root is one level above this script.
set "REPO_ROOT=%~dp0.."

REM Prepend repo root to PYTHONPATH so its package wins over the editable
REM install. Path quoting handles the space in "Claude_tomoapp optimisation".
set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"

REM Enable the perf-report-on-stderr if the first arg is --perf.
if /I "%~1"=="--perf" (
    set "TOMOAPP_PERF=1"
    shift
)

REM Resolve python from the tomoapp_claude conda env so the user does not
REM need to "conda activate" first.
set "CONDA_PY=%USERPROFILE%\miniconda3\envs\tomoapp_claude\python.exe"
if not exist "%CONDA_PY%" (
    echo ERROR: Could not find tomoapp_claude python at %CONDA_PY%
    echo Edit launch_app.bat and set CONDA_PY to the right path, or
    echo "conda activate tomoapp_claude" first and re-run.
    exit /b 1
)

"%CONDA_PY%" -m tomography_session_browser.main %*

endlocal
