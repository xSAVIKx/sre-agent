# Facilitator notes

## One week before

Send the **"Before the workshop"** section of [README.md](README.md) to the attendees.

The `uv sync` download is the largest risk on the venue wifi. No other task needs the network.

Docker and a `GEMINI_API_KEY` are **optional** for attendees. All steps work offline in the
deterministic simulation.

## The day before

1. Do these commands:

   ```bash
   uv run workshop/check.py 0
   git switch --detach step-00 && uv run workshop/check.py all   # 1-5 must all be ❌
   git switch --detach step-05 && uv run workshop/check.py all   # 1-5 must all be ✅
   ```

2. For the production demo in step 6, run `./bootstrap.sh && ./deploy.sh` **the day before**.
3. Record the URLs that `deploy.sh` shows.
4. After the session, run `./cleanup.sh`.

The first deployment into a new project takes approximately 6 minutes. A redeployment takes
approximately 3 minutes. All four services build and deploy in parallel on the prebuilt
dependency image.

## Timeline (90 minutes)

| Time | Item | Notes |
|:--|:--|:--|
| 0:00 | Introduction and demo | Use `step-05`. Start the chat UI and diagnose an incident. Show the SEV1 badge. Download the post-mortem. Then start the build. |
| 0:10 | Step 0 | All attendees are on `step-00`, and `check.py 0` shows `ok`. Explain the architecture table. Show the "blocked by the safety policy" line. Tell attendees that step 4 fixes it. |
| 0:20 | Step 1 | ADK workflow: draw the chain. Explain why `fetch_telemetry` is code and not an agent. Explain the scripted model: the ADK code is real without a key. |
| 0:32 | Step 2 | ADK agent: show a tool docstring. If an attendee has a key, show the tool calls that Gemini chooses. |
| 0:44 | Step 3 | A2A: open the agent card in the browser. Explain that a skill description is for another agent's model. |
| 0:56 | Step 4 | A2A client and safety. Before you show the policy, ask: "What can the agent do with the default SDK policy?" Then all attendees open the chat. |
| 1:08 | Step 5 | A2UI: all attendees click **Diagnose** on an incident. This step has the most visible result. Keep sufficient time for it. |
| 1:20 | Step 6 | Show the Cloud Run demo (deployed before). Explain the IAM split, the skill and the exercises. |

**If you are late:** steps 2 and 3 are the easiest to skip. Apply the two solutions, then explain
them. Step 4 is necessary for the chat, so do not skip it.

```bash
uv run workshop/step.py solve 2
uv run workshop/step.py solve 3
```

## Common problems

| Symptom | Fix |
|:--|:--|
| `check.py 0` shows that an import is missing | Run `uv sync --all-packages`. The `--all-packages` flag is necessary. |
| `The diagnose_sre tool call was blocked by the safety policy` | This is correct before step 4. Use `simulate_incident.py --engine-only`. |
| `Error: Failed to contact SRE Sub-Agent: TODO(step-4)` | This is correct before step 4: the Orchestrator does not call the SRE agent yet. |
| The report has no **Root Cause Analysis** section | This is correct before step 1: the workflow stops after the TraceAnalyzer. |
| Port 8080 is in use | Use `uv run workshop/chat.py --port 8081` and open `http://localhost:8081/chat`. |
| The cloud demo finds no anomalous trace | Cloud Trace receives spans with a delay. After `curl .../api/gateway?trigger_error=true`, wait approximately 2 minutes. Then ask the agent. |
| The chat shows "All systems are healthy" | There is no incident telemetry. From the repository root, run `uv run simulate_incident.py` first. |
| An attendee with a key gets different text | This is correct. Gemini writes the text. The tools make the cascade table and the post-mortem, so these are the same. |
| An attendee is behind or lost | `uv run workshop/step.py status` shows their progress. `uv run workshop/step.py goto N` commits their changes and starts the step N on a new branch. |
| `solve N` reports that the solution does not fit | The attendee changed the code of the step. Use `goto N+1`. |

## Maintaining the steps

The completed code is the source of truth. The steps come from the completed code:

* [`build_steps.py`](build_steps.py) contains two items for each step: the solution code and the
  TODO code that replaces it.
* `uv run python workshop/build_steps.py` writes the `steps/*/solution.patch` files again. Commit
  them.
* `uv run python workshop/build_steps.py --branch` builds the linear `workshop` branch and the
  local `step-00`…`step-05` tags again from `HEAD`. It does not change your working tree.
* To publish, run `git push -f origin workshop` and `git push -f origin 'refs/tags/step-*'`.

CI generates the patches again. If a patch is out of date, CI fails. Thus, CI finds a refactor
that moves the code of a step before the refactor breaks the workshop. If this occurs, update the
`solution` string of that step in `build_steps.py`.
