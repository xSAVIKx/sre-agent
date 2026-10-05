---
name: sre-agent-setup
description: Sets up this repository (the SRE agent) on the user's computer for local use or for the workshop. It installs uv and the dependencies, checks the setup, runs the incident simulation and starts the web chat. Use when the user asks to install, set up, prepare or fix their environment for this project or its workshop, or when a setup command fails. For deployment to Google Cloud, use the sre-agent-deploy skill.
---

# Set up the SRE agent

You help the user to install this repository and to run it on their computer. The local setup
needs no GCP account and no API key.

## Rules

* Run one command at a time. Read its output before you run the next command.
* Do not use `sudo` or install system packages without the user's approval. Show the command and
  ask first.
* Do not ask for the `GEMINI_API_KEY` value, and do not write it in a file or show it. If the user
  wants Gemini, tell them how to set the variable themselves.
* Use the commands below. They work on macOS, Linux and Windows (PowerShell). Do not use
  `VAR=value command` syntax: it fails in PowerShell.
* Speak in short, plain sentences. At the end, tell the user what works and what to do next.

## Procedure

1. **Find the operating system** and the shell (macOS, Linux, Windows PowerShell, Git Bash or WSL).

2. **Check the tools.** Run `git --version` and `uv --version`.
   * uv is missing: run the installer of this repository. It installs uv, then it installs
     the dependencies and checks the setup:
     * macOS and Linux: `sh install.sh`
     * Windows: `powershell -ExecutionPolicy ByPass -File install.ps1`
     Then go to step 5.
   * git is missing: git is necessary only for the workshop. Tell the user how to install it:
     `xcode-select --install` (macOS), `winget install --id Git.Git -e` (Windows), or the
     package manager (Linux).

3. **Install the dependencies:** `uv sync --all-packages`. uv also gets Python 3.11+ if necessary.

4. **For the workshop only:** make sure that the tag `step-00` exists (`git tag -l step-00`). If
   not, run `git fetch origin --tags`. If the user has no branch `my-work`, run
   `git switch -c my-work step-00`. Do not switch branches when `git status --porcelain` shows
   changes: ask the user first.

5. **Check the setup:** `uv run workshop/check.py 0`. All lines must say `ok`. Yellow lines are
   optional. Fix red lines with the table below.

6. **Run the simulation:** `uv run simulate_incident.py`. The output must contain
   `Identified Bottleneck` and `Incident Post-Mortem`.

   On the workshop branch, the steps are not solved yet. This is correct, and it is not a setup
   problem. Do not solve the workshop steps for the user:
   * Before step 4, the Orchestrator's safety policy blocks the tool call. Use
     `uv run simulate_incident.py --engine-only` to check the setup.
   * Before step 1, the bottleneck is the wrong span (`/api/gateway`, 0.0 %).

7. **Explain the result** in 3 to 5 sentences: which span is the bottleneck, its share of the
   request time, and the root cause from the post-mortem.

8. **Offer the web chat:** `uv run workshop/chat.py`, then open <http://localhost:8080/chat>.
   It runs until the user presses Ctrl+C, so start it only when the user asks.

## Problems and fixes

| Symptom | Fix |
|:--|:--|
| `uv: command not found` just after the installation | Open a new terminal. The uv installer changed the PATH for new terminals only. |
| `uv sync` fails with an SSL or certificate error (company proxy) | Set `UV_NATIVE_TLS=1` and run `uv sync --all-packages` again. |
| `uv sync` is very slow or times out | Network problem. Run it again: uv keeps what it downloaded. |
| `import ...: failed` in `check.py 0` | Run `uv sync --all-packages` from the repository root. |
| `port 8080: in use` | Stop the other program, or use `uv run workshop/chat.py --port 8090`. |
| `git: no workshop steps` | Run `git fetch origin --tags`. |
| Windows: `running scripts is disabled on this system` | Use `powershell -ExecutionPolicy ByPass -File install.ps1`. |
| Colors show as `[32m` | The terminal has no ANSI colors. This is not a problem. |
