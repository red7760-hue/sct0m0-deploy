<#
.SYNOPSIS
    Sets up this machine to run sct0m0_init_raw.py / sct0m0_protocol.py,
    installing Python itself if it isn't already present.

.DESCRIPTION
    Run this AFTER copying sct0m0_init_raw.py and sct0m0_protocol.py onto
    the machine and placing Setup-SCT0M0.ps1 in the SAME folder as those
    two files. You do NOT need to pre-install Python - this script will
    fetch and silently install it if needed, using whichever of the
    following is available on this machine, in order:

        1. winget (Windows Package Manager) - preferred, built into
           Windows 11 / recent Windows 10 / Server 2025.
        2. Direct download of the official python.org installer
           (requires internet access, which you've confirmed exists
           at the site) - used if winget isn't available.

    Then it:
        3. Installs pyserial via pip.
        4. Verifies both required .py files are present.
        5. Optionally runs a connection smoke test (-TestRun switch).

    No GitHub, no package registry account, nothing beyond
    python.org / Microsoft's own winget repository is contacted.

.USAGE
    Just set up (no hardware test):
        .\Setup-SCT0M0.ps1

    Set up AND immediately try connecting to the device:
        .\Setup-SCT0M0.ps1 -TestRun

    Force a specific Python version via direct download (skips winget):
        .\Setup-SCT0M0.ps1 -ForceDirectDownload -PythonVersion 3.12.10

.NOTES
    - Must be run from an ELEVATED PowerShell window (Run as Administrator)
      if Python needs to be installed system-wide. If you skip elevation,
      a per-user install will be attempted instead.
    - If you get an "execution of scripts is disabled" error, run this
      once first (as the same user):
          Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>

[CmdletBinding()]
param(
    [switch]$TestRun,
    [switch]$ForceDirectDownload,
    [string]$PythonVersion = "3.12.10"
)

$ErrorActionPreference = "Stop"

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

# ----------------------------------------------------------------------
# 0. Resolve script directory
# ----------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir
Write-Host "Working directory: $ScriptDir"

$IsAdmin = Test-IsAdmin
Write-Host "Running elevated: $IsAdmin"

# ----------------------------------------------------------------------
# 1. Look for an existing, real Python interpreter
# ----------------------------------------------------------------------
function Find-RealPython {
    foreach ($candidate in @("py", "python", "python3")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }

        # Guard against Windows Store "App Execution Alias" stubs, which
        # exist on PATH but don't run real Python (they may silently
        # open the Store, or print nothing useful).
        $verOutput = & $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $verOutput -match "^Python \d+\.\d+") {
            return $candidate
        }
    }
    return $null
}

Write-Step "Looking for an existing Python install"
$PythonCmd = Find-RealPython

if ($PythonCmd) {
    $ver = & $PythonCmd --version 2>&1
    Write-Ok "Found working Python: '$PythonCmd' -> $ver"
} else {
    Write-Host "    No working Python found - will install one." -ForegroundColor Yellow

    # ------------------------------------------------------------------
    # 1a. Try winget first
    # ------------------------------------------------------------------
    $wingetAvailable = $false
    if (-not $ForceDirectDownload) {
        Write-Step "Checking for winget"
        $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
        if ($wingetCmd) {
            Write-Ok "winget is available"
            $wingetAvailable = $true
        } else {
            Write-Host "    winget not found on this machine." -ForegroundColor Yellow
        }
    }

    if ($wingetAvailable) {
        Write-Step "Installing Python via winget"

        # winget can hang waiting for an interactive Y/N on source
        # agreements if this is its very first run on the machine, even
        # with --accept-source-agreements (this is a known winget quirk
        # in unattended contexts, not a bug in this script). We run it as
        # a background job with a hard timeout so an unattended site
        # never gets stuck waiting forever - if it doesn't finish in
        # time, we kill it and fall back to direct download instead.
        $wingetJob = Start-Job -ScriptBlock {
            winget install --id Python.Python.3.12 --source winget `
                --accept-package-agreements --accept-source-agreements `
                --disable-interactivity --silent
            return $LASTEXITCODE
        }

        $finished = Wait-Job $wingetJob -Timeout 180
        if (-not $finished) {
            $timeoutMsg = "winget did not finish within 3 minutes (likely stuck on a " +
                "first-run prompt it can't show in this unattended session) - " +
                "stopping it and falling back to direct download."
            Write-Fail $timeoutMsg
            Stop-Job $wingetJob -ErrorAction SilentlyContinue
            Remove-Job $wingetJob -Force -ErrorAction SilentlyContinue
            $wingetAvailable = $false
        } else {
            $wingetExit = Receive-Job $wingetJob
            Remove-Job $wingetJob -Force -ErrorAction SilentlyContinue
            if ($wingetExit -ne 0) {
                Write-Fail "winget install failed (exit $wingetExit) - will fall back to direct download."
                $wingetAvailable = $false
            } else {
                Write-Ok "winget install completed"
                # winget installs into a fresh PATH entry - refresh this
                # process's view of PATH from the registry so we can find
                # it without needing a new PowerShell session.
                $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
                $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
                $env:Path = "$machinePath;$userPath"
            }
        }
    }

    # ------------------------------------------------------------------
    # 1b. Fall back to direct download from python.org if winget
    #     wasn't available or failed.
    # ------------------------------------------------------------------
    if (-not $wingetAvailable) {
        Write-Step "Downloading Python $PythonVersion directly from python.org"

        $installerUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
        $installerPath = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"

        try {
            Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
            Write-Ok "Downloaded installer to $installerPath"
        } catch {
            Write-Fail "Could not download Python installer from $installerUrl : $_"
            Write-Host ""
            Write-Host "Check internet access at this site, or manually download Python from" -ForegroundColor Yellow
            Write-Host "https://www.python.org/downloads/windows/ and install it, then re-run this script." -ForegroundColor Yellow
            exit 1
        }

        Write-Step "Installing Python $PythonVersion silently"
        # InstallAllUsers=1 needs elevation; fall back to per-user if not admin.
        if ($IsAdmin) {
            $installArgs = "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0"
        } else {
            Write-Host "    Not running elevated - installing for current user only." -ForegroundColor Yellow
            $installArgs = "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0"
        }

        $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-Fail "Python installer exited with code $($proc.ExitCode)."
            exit 1
        }
        Write-Ok "Python installed"

        # Refresh PATH in this process so we can find the new install
        # without needing to open a new PowerShell window.
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machinePath;$userPath"

        Remove-Item $installerPath -ErrorAction SilentlyContinue
    }

    # Re-check for Python now that something was installed
    Write-Step "Re-checking for Python after install"
    $PythonCmd = Find-RealPython
    if (-not $PythonCmd) {
        Write-Fail "Still cannot find a working Python after installation."
        Write-Host "Try closing this PowerShell window, opening a NEW one, and re-running this script" -ForegroundColor Yellow
        Write-Host "(PATH changes sometimes need a fresh session to take effect)." -ForegroundColor Yellow
        exit 1
    }
    $ver = & $PythonCmd --version 2>&1
    Write-Ok "Python now available: '$PythonCmd' -> $ver"
}

# ----------------------------------------------------------------------
# 2. Confirm pip is available, then install pyserial
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
# 3. Confirm required script files are present
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
# 4. Syntax-check both files compile cleanly on this machine's Python
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
# 5. Optional: actually try connecting to the device
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
Write-Host "To run the initializer manually later:" -ForegroundColor Cyan
Write-Host "    cd `"$ScriptDir`"" -ForegroundColor White
Write-Host "    $PythonCmd sct0m0_init_raw.py" -ForegroundColor White
