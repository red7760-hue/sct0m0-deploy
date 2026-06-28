<#
.SYNOPSIS
    Sets up this machine to run sct0m0_init_raw.py / sct0m0_protocol.py,
    installing Python 3.13 via winget if it isn't already present.

.DESCRIPTION
    Run this AFTER copying sct0m0_init_raw.py and sct0m0_protocol.py onto
    the machine and placing Setup-SCT0M0.ps1 in the SAME folder as those
    two files.

    Requires winget to be present on the machine (built into Windows 11
    and recent Windows 10 / Server 2025 by default). If winget isn't
    available, this script will tell you and stop rather than guessing
    at an alternative.

    Steps performed:
        1. Install Python 3.13 via "winget install Python.Python.3.13"
           (skipped if a working Python is already present).
        2. Confirm python.exe actually exists on disk afterward (don't
           just trust winget's exit code).
        3. Install pyserial via pip.
        4. Verify both required .py files are present and compile.
        5. Optionally run a connection smoke test (-TestRun switch).

.USAGE
    Just set up (no hardware test):
        .\Setup-SCT0M0.ps1

    Set up AND immediately try connecting to the device:
        .\Setup-SCT0M0.ps1 -TestRun

.NOTES
    - Run from an ELEVATED PowerShell window (Run as Administrator) for
      a machine-wide install. Without elevation, winget installs for the
      current user only.
    - If you get an "execution of scripts is disabled" error, run this
      once first (as the same user):
          Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
    - This script works around the one real gotcha with winget+Python:
      a brand new PATH entry is NOT visible in the terminal that did the
      installing (PowerShell caches environment variables for the life
      of the session - this is normal Windows behavior, not a bug). The
      script handles this for itself by finding python.exe directly on
      disk and using its full path, so you don't need to reopen
      PowerShell partway through. You WILL still need a new PowerShell
      window afterward if you want to type a bare "python" command later.
#>

[CmdletBinding()]
param(
    [switch]$TestRun
)

$ErrorActionPreference = "Stop"

$PythonPackageId = "Python.Python.3.13"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "    OK: $msg" -ForegroundColor Green
}

function Write-Fail($msg) {
    Write-Host "    FAILED: $msg" -ForegroundColor Red
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-RealPython {
    # Guards against Windows Store "App Execution Alias" stubs. These
    # exist on PATH (Get-Command finds them) but aren't real Python -
    # running one throws a terminating-style error ("Python was not
    # found; run without arguments to install from the Microsoft
    # Store...") rather than just failing quietly. With
    # $ErrorActionPreference = "Stop" set globally, that error would
    # otherwise kill this whole script, so we explicitly catch it here
    # and treat it the same as "this candidate doesn't work, try the
    # next one" - which is exactly what should happen.
    foreach ($candidate in @("py", "python", "python3")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }

        try {
            $verOutput = & $candidate --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $verOutput -match "^Python \d+\.\d+") {
                return $candidate
            }
        } catch {
            # Store alias stub or similar - not a real Python, skip it.
            continue
        }
    }
    return $null
}

function Find-PythonExeOnDisk {
    # After a winget install, search the well-known install locations
    # directly rather than relying on PATH (which won't be refreshed in
    # this terminal session - see .NOTES above). python.exe lives
    # directly inside the versioned folder (e.g. ...\Python313\python.exe),
    # so each glob below just needs to resolve the folder, then check
    # for the exe inside it - no recursion needed.
    $searchGlobs = @(
        "C:\Program Files\Python3*",
        "C:\Program Files (x86)\Python3*",
        "$env:LOCALAPPDATA\Programs\Python\Python3*"
    )
    $candidates = foreach ($glob in $searchGlobs) {
        Get-ChildItem -Path $glob -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $exePath = Join-Path $_.FullName "python.exe"
            if (Test-Path $exePath) {
                Get-Item $exePath
            }
        }
    }
    $found = $candidates | Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

# ----------------------------------------------------------------------
# 0. Resolve script directory
# ----------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir
Write-Host "Working directory: $ScriptDir"

$IsAdmin = Test-IsAdmin
Write-Host "Running elevated: $IsAdmin"

# ----------------------------------------------------------------------
# 1. Confirm winget exists - this script does not fall back to anything
#    else, by design.
# ----------------------------------------------------------------------
Write-Step "Checking for winget"
$wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
if (-not $wingetCmd) {
    Write-Fail "winget was not found on this machine."
    Write-Host ""
    Write-Host "winget ships with Windows 11 and recent Windows 10 by default." -ForegroundColor Yellow
    Write-Host "On older or Server builds, install 'App Installer' from the Microsoft" -ForegroundColor Yellow
    Write-Host "Store, or see https://aka.ms/getwinget for manual install options." -ForegroundColor Yellow
    exit 1
}
Write-Ok "winget is available"

# ----------------------------------------------------------------------
# 2. Look for an existing, real Python interpreter - skip install if
#    one already works.
# ----------------------------------------------------------------------
Write-Step "Looking for an existing Python install"
$PythonCmd = Find-RealPython

if ($PythonCmd) {
    $ver = & $PythonCmd --version 2>&1
    Write-Ok "Found working Python: '$PythonCmd' -> $ver"
} else {
    Write-Host "    No working Python found - installing $PythonPackageId via winget." -ForegroundColor Yellow

    # winget can hang on its very first run waiting for an interactive
    # Y/N on source agreements, even with --accept-source-agreements
    # (a known winget quirk, not a bug in this script). Run it as a
    # background job with a hard timeout so an unattended machine never
    # gets stuck waiting forever for input it can't provide.
    Write-Step "Installing $PythonPackageId via winget"

    $wingetJob = Start-Job -ScriptBlock {
        param($PackageId)
        winget install --id $PackageId --source winget --exact `
            --accept-package-agreements --accept-source-agreements `
            --disable-interactivity --silent
        return $LASTEXITCODE
    } -ArgumentList $PythonPackageId

    $finished = Wait-Job $wingetJob -Timeout 180
    if (-not $finished) {
        Stop-Job $wingetJob -ErrorAction SilentlyContinue
        Remove-Job $wingetJob -Force -ErrorAction SilentlyContinue
        Write-Fail "winget did not finish within 3 minutes."
        Write-Host "    This usually means it's stuck on a first-run prompt it can't show" -ForegroundColor Yellow
        Write-Host "    in this unattended session. Try running 'winget list' once manually" -ForegroundColor Yellow
        Write-Host "    on this machine (interactively) to accept any source agreements," -ForegroundColor Yellow
        Write-Host "    then re-run this script." -ForegroundColor Yellow
        exit 1
    }

    $wingetExit = Receive-Job $wingetJob
    Remove-Job $wingetJob -Force -ErrorAction SilentlyContinue
    if ($wingetExit -ne 0) {
        Write-Fail "winget install failed (exit code $wingetExit)."
        exit 1
    }
    Write-Ok "winget reported a successful install"

    # Don't just trust winget's exit code - confirm python.exe actually
    # exists on disk, polling briefly in case of any write-finalization
    # delay, then use its FULL PATH directly rather than relying on
    # PATH/$env:Path (which will not be refreshed in this terminal
    # session no matter what we do - that's normal Windows behavior).
    Write-Step "Confirming python.exe exists on disk"
    $deadline = (Get-Date).AddSeconds(60)
    $foundExe = $null
    while (-not $foundExe -and (Get-Date) -lt $deadline) {
        $foundExe = Find-PythonExeOnDisk
        if (-not $foundExe) { Start-Sleep -Seconds 2 }
    }

    if (-not $foundExe) {
        Write-Fail "winget reported success, but no python.exe was found in the expected locations."
        Write-Host "    Checked: C:\Program Files\Python3*, C:\Program Files (x86)\Python3*," -ForegroundColor Yellow
        Write-Host "    and $env:LOCALAPPDATA\Programs\Python\Python3*" -ForegroundColor Yellow
        Write-Host "    Open a NEW PowerShell window and try 'winget list $PythonPackageId'" -ForegroundColor Yellow
        Write-Host "    to see where it actually installed, if anywhere." -ForegroundColor Yellow
        exit 1
    }

    $PythonCmd = $foundExe
    $ver = & $PythonCmd --version 2>&1
    Write-Ok "Confirmed working Python: '$PythonCmd' -> $ver"
}

# ----------------------------------------------------------------------
# 3. Confirm pip is available, then install pyserial
# ----------------------------------------------------------------------
Write-Step "Checking pip"
& $PythonCmd -m pip --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip is not available for '$PythonCmd'."
    exit 1
}
Write-Ok "pip is available"

Write-Step "Installing/upgrading pyserial"
& $PythonCmd -m pip install --upgrade pyserial 2>&1 | ForEach-Object { Write-Host "    $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install pyserial failed (see output above)."
    exit 1
}
Write-Ok "pyserial installed"

Write-Step "Verifying pyserial imports correctly"
$importCheck = & $PythonCmd -c "import serial; print(serial.VERSION)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pyserial did not import cleanly: $importCheck"
    exit 1
}
Write-Ok "pyserial version $importCheck importable"

# ----------------------------------------------------------------------
# 4. Confirm required script files are present
# ----------------------------------------------------------------------
Write-Step "Checking required files"

$RequiredFiles = @("sct0m0_protocol.py", "sct0m0_init_raw.py")
$missing = @()
foreach ($f in $RequiredFiles) {
    $path = Join-Path $ScriptDir $f
    if (Test-Path $path) {
        Write-Ok "$f present"
    } else {
        Write-Fail "$f is MISSING from $ScriptDir"
        $missing += $f
    }
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Copy the missing file(s) into this same folder, then re-run this script." -ForegroundColor Yellow
    exit 1
}

# ----------------------------------------------------------------------
# 5. Syntax-check both files compile cleanly on this machine's Python
# ----------------------------------------------------------------------
Write-Step "Verifying script files compile"
foreach ($f in $RequiredFiles) {
    & $PythonCmd -m py_compile (Join-Path $ScriptDir $f)
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "$f failed to compile - see error above."
        exit 1
    }
    Write-Ok "$f compiles cleanly"
}

# ----------------------------------------------------------------------
# 6. Optional: actually try connecting to the device
# ----------------------------------------------------------------------
if ($TestRun) {
    Write-Step "Running sct0m0_init_raw.py against the device"
    & $PythonCmd (Join-Path $ScriptDir "sct0m0_init_raw.py")
    $testExit = $LASTEXITCODE
    if ($testExit -eq 0) {
        Write-Ok "Init script ran without a fatal error (check output above and sct0m0_init.log for the real result)"
    } else {
        Write-Fail "Init script exited with code $testExit - check output above and sct0m0_init.log"
    }
}

Write-Host ""
Write-Host "Setup complete on this machine ($env:COMPUTERNAME)." -ForegroundColor Cyan
Write-Host "To run the initializer manually later, in a NEW PowerShell window:" -ForegroundColor Cyan
Write-Host "    cd `"$ScriptDir`"" -ForegroundColor White
Write-Host "    python sct0m0_init_raw.py" -ForegroundColor White
Write-Host ""
Write-Host "(A NEW window is needed if Python was just installed by this script -" -ForegroundColor Yellow
Write-Host "PATH changes need a fresh PowerShell session to be visible. This script" -ForegroundColor Yellow
Write-Host "already worked around that for everything it just did.)" -ForegroundColor Yellow
