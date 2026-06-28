<#
.SYNOPSIS
    Downloads the SCT0M0 deployment files from GitHub and runs setup -
    no USB stick or email transfer needed, just this one script.

.DESCRIPTION
    Run this on a brand-new machine that has nothing on it yet. It will:
      1. Create a local folder (default C:\SCT0M0).
      2. Download sct0m0_protocol.py, sct0m0_init_raw.py, and
         Setup-SCT0M0.ps1 from your GitHub repo's raw file URLs.
      3. Call Setup-SCT0M0.ps1, which installs Python (if needed),
         installs pyserial, and verifies everything.

    EDIT THE TWO VARIABLES BELOW ($GitHubUser and $GitHubRepo) to match
    your actual GitHub username and repository name before using this.

.USAGE
    Just download + set up (no hardware test):
        irm https://raw.githubusercontent.com/YOURUSERNAME/YOURREPO/main/Bootstrap-SCT0M0.ps1 | iex

    Or, if you've saved this file locally first:
        .\Bootstrap-SCT0M0.ps1

    To also run a connection smoke test against the hardware:
        .\Bootstrap-SCT0M0.ps1 -TestRun

.NOTES
    This assumes a PUBLIC GitHub repo - raw.githubusercontent.com serves
    public repo files with no authentication needed. If the repo is ever
    made private, this script (and the one-liner above) will stop
    working without a GitHub personal access token added to the request.
#>

[CmdletBinding()]
param(
    [switch]$TestRun,
    [string]$InstallDir = "C:\SCT0M0"
)

$ErrorActionPreference = "Stop"

# ----------------------------------------------------------------------
# EDIT THESE TWO VALUES for your repo
# ----------------------------------------------------------------------
$GitHubUser = "red7760-hue"
$GitHubRepo = "sct0m0-deploy"
$Branch     = "main"

$BaseUrl = "https://raw.githubusercontent.com/$GitHubUser/$GitHubRepo/$Branch"

$FilesToFetch = @(
    "sct0m0_protocol.py",
    "sct0m0_init_raw.py",
    "Setup-SCT0M0.ps1"
)

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

# ----------------------------------------------------------------------
# 1. Create the install directory
# ----------------------------------------------------------------------
Write-Step "Preparing install directory: $InstallDir"
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}
Write-Ok "Directory ready"

# ----------------------------------------------------------------------
# 2. Download each file from GitHub
# ----------------------------------------------------------------------
Write-Step "Downloading files from GitHub ($GitHubUser/$GitHubRepo, branch $Branch)"

foreach ($file in $FilesToFetch) {
    $url = "$BaseUrl/$file"
    $dest = Join-Path $InstallDir $file
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        Write-Ok "Downloaded $file"
    } catch {
        Write-Fail "Could not download $file from $url"
        Write-Host "    Error: $_" -ForegroundColor Red
        Write-Host ""
        Write-Host "Check that:" -ForegroundColor Yellow
        Write-Host "  - The repo is public" -ForegroundColor Yellow
        Write-Host "  - GitHubUser/GitHubRepo/Branch at the top of this script are correct" -ForegroundColor Yellow
        Write-Host "  - The filename matches exactly (case-sensitive) what's in the repo" -ForegroundColor Yellow
        Write-Host "  - This machine has internet access" -ForegroundColor Yellow
        exit 1
    }
}

# ----------------------------------------------------------------------
# 3. Hand off to Setup-SCT0M0.ps1
# ----------------------------------------------------------------------
Write-Step "Running Setup-SCT0M0.ps1"

$setupScript = Join-Path $InstallDir "Setup-SCT0M0.ps1"

if ($TestRun) {
    & $setupScript -TestRun
} else {
    & $setupScript
}

$setupExit = $LASTEXITCODE
if ($setupExit -eq 0) {
    Write-Host ""
    Write-Host "Bootstrap complete. Files are in $InstallDir" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Fail "Setup-SCT0M0.ps1 exited with code $setupExit - see output above."
}
