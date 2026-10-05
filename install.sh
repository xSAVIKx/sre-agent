#!/bin/sh
#
# install.sh - Installs the SRE agent on macOS and Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.sh | sh
#
# The script needs only curl (or wget) and tar. It does these steps:
#   1. It installs uv, if uv is not on the PATH. uv installs Python 3.11+ when necessary.
#   2. It gets the repository: with git if git is available, or else as a tarball.
#      When you run it inside a checkout, it uses that checkout.
#   3. It installs the dependencies (uv sync --all-packages).
#   4. It checks the setup (uv run workshop/check.py 0).
#
# Options (or the environment variables in brackets):
#   --dir DIR    The directory for the repository [SRE_AGENT_DIR]. Default: ./sre-agent.
#   --ref REF    The branch or tag to get [SRE_AGENT_REF]. Default: master.
#   --workshop   Prepare for the workshop [SRE_AGENT_WORKSHOP=1]: start your own branch my-work at
#                the tag step-00. This needs git.
#   --agent      After the installation, start the Antigravity CLI (agy) with the setup skill.
#
# To give options through a pipe, use: curl -LsSf <url> | sh -s -- --dir ~/sre-agent
#
# Windows: use install.ps1.

set -eu

REPO="${SRE_AGENT_REPO:-https://github.com/xSAVIKx/sre-agent}"
DIR="${SRE_AGENT_DIR:-}"
REF="${SRE_AGENT_REF:-master}"
WORKSHOP="${SRE_AGENT_WORKSHOP:-}"
START_AGENT=false
# Keep in step with UV_VERSION in .github/workflows/ci.yml.
UV_VERSION="0.12.23"

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --workshop) WORKSHOP=1; shift ;;
        --agent) START_AGENT=true; shift ;;
        -h|--help) echo "Usage: install.sh [--dir DIR] [--ref REF] [--workshop] [--agent]. See the comments at the top of install.sh."; exit 0 ;;
        *) echo "Unknown option: $1 (use --help)" >&2; exit 2 ;;
    esac
done

if [ -t 1 ]; then
    BLUE='\033[0;34m' GREEN='\033[0;32m' YELLOW='\033[0;33m' RED='\033[0;31m' NC='\033[0m'
else
    BLUE='' GREEN='' YELLOW='' RED='' NC=''
fi
step() { printf "${BLUE}==>${NC} %s\n" "$1"; }
ok() { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }
fail() { printf "${RED}✗ %s${NC}\n" "$1" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

download() { # download URL -> stdout
    if has curl; then curl -LsSf "$1"; elif has wget; then wget -qO- "$1"; else fail "Install curl or wget."; fi
}

is_checkout() { [ -f "$1/pyproject.toml" ] && [ -f "$1/workshop/check.py" ]; }

git_hint() {
    case "$(uname -s)" in
        Darwin) echo "Install git with: xcode-select --install" ;;
        *) echo "Install git with your package manager, for example: sudo apt install git" ;;
    esac
}

if [ -n "$WORKSHOP" ]; then
    has git || fail "The workshop needs git. $(git_hint)"
    REF=workshop
fi

# 1. uv
step "Checking uv"
NEW_UV=false
if ! has uv; then
    NEW_UV=true
    download "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh
    # The uv installer adds its directory to your shell profile. This shell needs it now.
    PATH="${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}:$HOME/.local/bin:$PATH"
    export PATH
    has uv || fail "uv is installed, but it is not on the PATH. Open a new terminal and run this script again."
fi
ok "$(uv --version)"

# 2. The repository
step "Getting the repository"
if [ -z "$DIR" ] && is_checkout .; then
    DIR=.
    ok "Using the checkout in $(pwd)"
elif [ -n "$DIR" ] && is_checkout "$DIR"; then
    ok "Using the checkout in $DIR"
else
    DIR="${DIR:-sre-agent}"
    if [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
        fail "$DIR exists and is not empty. Use --dir to choose another directory."
    fi
    if has git; then
        git clone --quiet --branch "$REF" "$REPO.git" "$DIR"
        ok "Cloned $REPO ($REF) into $DIR"
    else
        has tar || fail "Install git, or tar to unpack the download."
        mkdir -p "$DIR"
        download "$REPO/archive/$REF.tar.gz" | tar -xzf - -C "$DIR" --strip-components=1
        ok "Downloaded $REPO ($REF) into $DIR"
        warn "git is not installed. The workshop steps need git: install it to do the workshop."
    fi
fi
cd "$DIR"

if [ -n "$WORKSHOP" ]; then
    step "Preparing the workshop"
    git fetch --quiet --tags origin workshop
    if git rev-parse --verify --quiet my-work >/dev/null; then
        ok "Your branch my-work exists. To start again: git switch -c fresh step-00"
    elif [ -n "$(git status --porcelain)" ]; then
        warn "You have changes that are not committed. Commit or stash them, then run: git switch -c my-work step-00"
    else
        git switch --quiet -c my-work step-00
        ok "You are on your own branch my-work, at the start of the workshop (step-00)"
    fi
fi

# 3. Dependencies
step "Installing the dependencies (this can take a few minutes)"
uv sync --all-packages
ok "Dependencies installed"

# 4. Check
step "Checking the setup"
uv run workshop/check.py 0 || fail "The setup check failed. See the messages above."

printf "\n${GREEN}The SRE agent is ready in %s${NC}\n\n" "$(pwd)"
if [ "$NEW_UV" = true ]; then
    warn "uv is new on this computer: open a new terminal before you use it."
fi
cat <<EOF
Next steps:
  cd "$(pwd)"
  uv run simulate_incident.py                               # diagnose a simulated incident
  uv run workshop/chat.py                                   # the web chat: http://localhost:8080/chat

  Optional: export GEMINI_API_KEY=...  (https://aistudio.google.com/apikey) to use Gemini.
  Deploy to Google Cloud: ./bootstrap.sh, then ./deploy.sh (needs the gcloud CLI).
  Let an agent guide you: agy, then ask "set up the SRE agent". See INSTALL.md.
EOF

if [ "$START_AGENT" = true ]; then
    if has agy; then
        # The script can come from a pipe: give agy the terminal as its input.
        exec agy -i "Use the sre-agent-setup skill: check my setup, run the simulation and explain the result." </dev/tty
    fi
    warn "The Antigravity CLI (agy) is not installed: https://antigravity.google/download"
fi
