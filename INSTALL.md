# Install the SRE agent

You need only a terminal and an internet connection. The installer gets everything else:

* [uv](https://docs.astral.sh/uv/), which also gets Python 3.11 or later when necessary.
* The repository (with git if you have it, or as a download).
* All the dependencies.

You do not need administrator rights, Docker, Node.js, a GCP account or an API key.

## One command

**macOS and Linux:**

```bash
curl -LsSf https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.sh | sh
```

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.ps1 | iex"
```

The installer puts the repository in the directory `sre-agent`. Then it runs the setup check
`uv run workshop/check.py 0`. When all lines say `ok`, the installation is complete.

If uv was not on your computer before, **open a new terminal** before you use `uv`.

### For the workshop

The workshop needs git. Add the workshop option:

```bash
# macOS and Linux
curl -LsSf https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.sh | sh -s -- --workshop
```

```powershell
# Windows (PowerShell)
$env:SRE_AGENT_WORKSHOP = "1"
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/xSAVIKx/sre-agent/master/install.ps1 | iex"
```

With this option, the installer also creates your own branch `my-work` at the start of the
workshop (the tag `step-00`).

### Options

| `install.sh` | `install.ps1` | Environment variable | Effect |
|:--|:--|:--|:--|
| `--dir DIR` | `-Dir DIR` | `SRE_AGENT_DIR` | The directory for the repository. Default: `sre-agent`. |
| `--ref REF` | `-Ref REF` | `SRE_AGENT_REF` | The branch or tag to get. Default: `master`. |
| `--workshop` | `-Workshop` | `SRE_AGENT_WORKSHOP=1` | Prepare for the workshop (see above). |
| `--agent` | `-Agent` | | Start the Antigravity CLI with the setup skill after the installation. |

With a pipe (`| sh`, `| iex`), use the environment variables, or `sh -s -- <options>`.

You can also run the installer in a checkout that you already have: `sh install.sh` or
`powershell -ExecutionPolicy ByPass -File install.ps1`. It then uses that checkout.

## Use it

These commands are the same on all systems:

```bash
cd sre-agent
uv run simulate_incident.py   # makes an incident and diagnoses it in your terminal
uv run workshop/chat.py       # the web chat: open http://localhost:8080/chat
```

To use Gemini instead of the deterministic simulation, set `GEMINI_API_KEY`
([get a key](https://aistudio.google.com/apikey)):

* macOS and Linux: `export GEMINI_API_KEY=...`
* Windows (PowerShell): `$env:GEMINI_API_KEY = "..."`

## Let an agent set it up (optional)

This repository has two [Antigravity](https://antigravity.google) skills in `.agents/skills/`.
The Antigravity app and the Antigravity CLI (`agy`) find them automatically when you open the
repository:

| Skill | What the agent does |
|:--|:--|
| `sre-agent-setup` | Installs uv and the dependencies, checks the setup, prepares the workshop branch, runs the simulation, explains the result and fixes common problems. |
| `sre-agent-deploy` | Guides you through `bootstrap.sh`, `deploy.sh`, the verification and `cleanup.sh`. It asks for your approval before each step that costs money. |

The agent never asks for your API key: you set it yourself.

### Prompts that you can use

Open the repository in the Antigravity app, or run `agy` in the repository directory. Then use
one of these prompts:

```text
Set up this project on my computer, run the incident simulation and explain the result.
```

```text
Prepare my computer for the SRE agent workshop.
```

```text
`uv run workshop/check.py 0` shows red lines. Find the cause and fix it.
```

```text
Deploy the SRE agent to my Google Cloud project, and tell me how to check that it works.
```

To start the CLI with a prompt in one command:

```bash
agy -i "Prepare my computer for the SRE agent workshop."
```

Or let the installer start it at the end: `sh install.sh --agent` (macOS, Linux) or
`install.ps1 -Agent` (Windows).

## Deploy to Google Cloud

The deployment scripts (`bootstrap.sh`, `deploy.sh`, `cleanup.sh`) need bash and the
[gcloud CLI](https://cloud.google.com/sdk/docs/install). They do not need uv or Python.

* **macOS and Linux:** run them in your terminal. See [Deployment](README.md#️-deployment-to-google-cloud-run).
* **Windows:** use [Google Cloud Shell](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https://github.com/xSAVIKx/sre-agent).
  It has bash, git and gcloud, and you are already logged in. WSL and Git Bash also work.

## Problems and fixes

| Symptom | Fix |
|:--|:--|
| `uv: command not found` just after the installation | Open a new terminal. |
| `uv sync` fails with an SSL or certificate error (company proxy) | Set `UV_NATIVE_TLS=1` and run the installer again. |
| The download is slow or stops | Run the installer again. uv keeps what it downloaded. |
| `import ...: failed` in the setup check | Run `uv sync --all-packages` in the repository directory. |
| `port 8080: in use` | Stop the other program, or use `uv run workshop/chat.py --port 8090`. |
| `git: no workshop steps` | Run `git fetch origin --tags`. |
| `The workshop needs git` | Install git: `xcode-select --install` (macOS), `winget install --id Git.Git -e` (Windows) or your package manager (Linux). Then open a new terminal. |
| Windows: `running scripts is disabled on this system` | Use the `powershell -ExecutionPolicy ByPass ...` command above. |
