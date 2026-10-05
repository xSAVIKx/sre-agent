<#
.SYNOPSIS
    Installs the SRE agent on Windows.

.DESCRIPTION
    Run this command in PowerShell (5.1 or later). You do not need administrator rights:

        powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.ps1 | iex"

    The script needs only PowerShell. It does these steps:
      1. It installs uv, if uv is not on the PATH. uv installs Python 3.11+ when necessary.
      2. It gets the repository: with git if git is available, or else as a zip file.
         When you run it inside a checkout, it uses that checkout.
      3. It installs the dependencies (uv sync --all-packages).
      4. It checks the setup (uv run workshop/check.py 0).

    With "irm | iex", use the environment variables SRE_AGENT_DIR and SRE_AGENT_REF for the options.

    macOS and Linux: use install.sh.

.PARAMETER Dir
    The directory for the repository. Default: .\sre-agent.

.PARAMETER Ref
    The branch or tag to get. Default: master.

.PARAMETER Workshop
    Prepare for the workshop (or set SRE_AGENT_WORKSHOP=1): start your own branch my-work at the
    tag step-00. This needs git.

.PARAMETER Agent
    After the installation, start the Antigravity CLI (agy) with the setup skill.
#>
param(
    [string]$Dir = $env:SRE_AGENT_DIR,
    [string]$Ref = $(if ($env:SRE_AGENT_REF) { $env:SRE_AGENT_REF } else { "master" }),
    [switch]$Workshop = [bool]$env:SRE_AGENT_WORKSHOP,
    [switch]$Agent
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much faster without the progress bar
$Repo = if ($env:SRE_AGENT_REPO) { $env:SRE_AGENT_REPO } else { "https://github.com/xSAVIKx/sre-agent" }
# Keep in step with UV_VERSION in .github/workflows/ci.yml.
$UvVersion = "0.12.23"

function Step($Text) { Write-Host "==> $Text" -ForegroundColor Blue }
function Ok($Text) { Write-Host "OK  $Text" -ForegroundColor Green }
function Warn($Text) { Write-Host "!   $Text" -ForegroundColor Yellow }
function Fail($Text) { Write-Host "X   $Text" -ForegroundColor Red; exit 1 }
function Has($Command) { [bool](Get-Command $Command -ErrorAction SilentlyContinue) }
function Test-Checkout($Path) { (Test-Path "$Path\pyproject.toml") -and (Test-Path "$Path\workshop\check.py") }

# Native commands do not stop the script on failure: check their exit code.
function Invoke-Native([scriptblock]$Command, $Message) {
    & $Command
    if ($LASTEXITCODE -ne 0) { Fail $Message }
}

if ($Workshop) {
    if (-not (Has "git")) { Fail "The workshop needs git. Install it with: winget install --id Git.Git -e (then open a new terminal)" }
    $Ref = "workshop"
}

# 1. uv
Step "Checking uv"
$NewUv = $false
if (-not (Has "uv")) {
    $NewUv = $true
    Invoke-RestMethod "https://astral.sh/uv/$UvVersion/install.ps1" | Invoke-Expression
    # The uv installer adds its directory to your user PATH. This session needs it now.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Has "uv")) { Fail "uv is installed, but it is not on the PATH. Open a new terminal and run this script again." }
}
Ok (uv --version)

# Some dependencies (grpcio, cryptography, watchdog) have no Windows arm64 wheels, and grpcio
# cannot be built there. On arm64, use x64 Python: Windows runs it with its built-in emulation.
# uv creates .venv with it, so later `uv run` and `uv sync` commands keep it.
if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") {
    $env:UV_PYTHON = "cpython-3.13-windows-x86_64-none"
    Ok "Windows on arm64: using x64 Python (some dependencies have no arm64 version)"
}

# 2. The repository
Step "Getting the repository"
if (-not $Dir -and (Test-Checkout ".")) {
    $Dir = "."
    Ok "Using the checkout in $(Get-Location)"
} elseif ($Dir -and (Test-Checkout $Dir)) {
    Ok "Using the checkout in $Dir"
} else {
    if (-not $Dir) { $Dir = "sre-agent" }
    if ((Test-Path $Dir) -and (Get-ChildItem $Dir -Force | Select-Object -First 1)) {
        Fail "$Dir exists and is not empty. Use -Dir (or SRE_AGENT_DIR) to choose another directory."
    }
    if (Has "git") {
        Invoke-Native { git clone --quiet --branch $Ref "$Repo.git" $Dir } "git clone failed."
        Ok "Cloned $Repo ($Ref) into $Dir"
    } else {
        $Zip = Join-Path ([IO.Path]::GetTempPath()) "sre-agent-$([guid]::NewGuid()).zip"
        $Unpacked = "$Zip.d"
        Invoke-WebRequest "$Repo/archive/$Ref.zip" -OutFile $Zip -UseBasicParsing
        Expand-Archive $Zip -DestinationPath $Unpacked
        New-Item -ItemType Directory -Force $Dir | Out-Null
        # The zip has one top folder (sre-agent-<ref>): move its content into $Dir.
        Get-ChildItem (Get-ChildItem $Unpacked)[0].FullName -Force | Move-Item -Destination $Dir
        Remove-Item $Zip, $Unpacked -Recurse -Force
        Ok "Downloaded $Repo ($Ref) into $Dir"
        Warn "git is not installed. The workshop steps need git: install it to do the workshop."
    }
}
Set-Location $Dir

if ($Workshop) {
    Step "Preparing the workshop"
    Invoke-Native { git fetch --quiet --tags origin workshop } "git fetch failed."
    git rev-parse --verify --quiet my-work *> $null
    if ($LASTEXITCODE -eq 0) {
        Ok "Your branch my-work exists. To start again: git switch -c fresh step-00"
    } elseif (git status --porcelain) {
        Warn "You have changes that are not committed. Commit or stash them, then run: git switch -c my-work step-00"
    } else {
        Invoke-Native { git switch --quiet -c my-work step-00 } "git switch failed."
        Ok "You are on your own branch my-work, at the start of the workshop (step-00)"
    }
}

# 3. Dependencies
Step "Installing the dependencies (this can take a few minutes)"
Invoke-Native { uv sync --all-packages } "uv sync failed. See the messages above."
Ok "Dependencies installed"

# 4. Check
Step "Checking the setup"
Invoke-Native { uv run workshop/check.py 0 } "The setup check failed. See the messages above."

Write-Host ""
Write-Host "The SRE agent is ready in $(Get-Location)" -ForegroundColor Green
Write-Host ""
if ($NewUv) { Warn "uv is new on this computer: open a new terminal before you use it." }
Write-Host @"
Next steps:
  cd "$(Get-Location)"
  uv run simulate_incident.py                       # diagnose a simulated incident
  uv run workshop/chat.py                           # the web chat: http://localhost:8080/chat

  Optional: `$env:GEMINI_API_KEY = "..."  (https://aistudio.google.com/apikey) to use Gemini.
  Deploy to Google Cloud: use Google Cloud Shell, WSL or Git Bash. See INSTALL.md.
  Let an agent guide you: agy, then ask "set up the SRE agent". See INSTALL.md.
"@

if ($Agent) {
    if (Has "agy") {
        agy -i "Use the sre-agent-setup skill: check my setup, run the simulation and explain the result."
    } else {
        Warn "The Antigravity CLI (agy) is not installed: https://antigravity.google/download"
    }
}
