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
| 0:20 | Step 1 | This is the main idea of the workshop: inclusive time and exclusive time. Draw the three bars. |
| 0:32 | Step 2 | Explain the mock-mode rule: when `MOCK_GCP=true`, each tool reads local JSON. |
| 0:44 | Step 3 | The code change is small. Use more time for the discussion: each agent gets a small set of tools. If an attendee has a key, show the log of that attendee on the screen. |
| 0:54 | Step 4 | This step is about safety. Before you show the answer, ask: "What can the agent do with the default SDK policy?" |
| 1:06 | Step 5 | All attendees open the chat UI. This step has the most visible result. Keep sufficient time for it. |
| 1:20 | Step 6 | Show the Cloud Run demo (deployed before). Explain the IAM split, the skill and the exercises. |

**If you are late:** steps 2 and 3 are the easiest to skip. Apply the two solutions, then explain
them:

```bash
uv run workshop/step.py solve 2
uv run workshop/step.py solve 3
```

## Common problems

| Symptom | Fix |
|:--|:--|
| `check.py 0` shows that an import is missing | Run `uv sync --all-packages`. The `--all-packages` flag is necessary. |
| `The diagnose_sre tool call was blocked by the safety policy` | This is correct before step 4. Use `simulate_incident.py --engine-only`. |
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
