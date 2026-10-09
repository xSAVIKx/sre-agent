---
name: sre-workshop-facilitator
description: Helps the facilitator prepare, run and close the SRE agent workshop in this repository. It checks that everything is ready (preflight), rebuilds and publishes the workshop branch, step tags and website, prepares the cloud demo, and cleans up after the event. Use when the user is the facilitator or maintainer and asks to prepare, check, publish, rehearse, run or clean up the workshop, or to change a workshop step. For attendee questions, use the sre-workshop-coach skill.
---

# Run the SRE agent workshop (facilitator)

You help the person who runs the workshop. Attendees use the sre-workshop-coach skill instead.

## How the workshop is built

* The finished code on `master` is the only source. `workshop/build_steps.py` has one
  solution/starter pair for each `TODO(step-N)`. From these pairs it makes:
  * `workshop/steps/0N-*/solution.patch` (committed), and
  * the `workshop` branch and the tags `step-00` … `step-05` (local until you push them).
* `workshop/check.py N` runs the tests of step N. `workshop/step.py` moves attendees between steps.
* The website is the Markdown of `workshop/`, `INSTALL.md` and `EXERCISES.md`. Build it with
  `uv run scripts/build_docs.py` (strict: a broken link fails). `.github/workflows/docs.yml`
  publishes it from `master` to <https://xsavikx.github.io/sre-agent/>.
* The facilitator notes are in `workshop/FACILITATOR.md`. The timeline and the common problems
  are there.

## Rules

* Ask before each action that changes something outside this computer: `git push` (above all
  `--force-with-lease`), merging a PR, a deployment (`deploy.sh`), `cleanup.sh`, or a change to
  the GitHub settings. Show the exact command first.
* Do not push other tags than `step-00` … `step-05`.
* Do not edit `solution.patch` files or the `workshop` branch by hand. Change the code on master,
  and update the solution string in `workshop/build_steps.py` if the change touches a step.
* Never show or write the `GEMINI_API_KEY` values.

## Prepare (the day before)

1. Make sure that you are on `master`, up to date, with no uncommitted changes.
2. Rebuild the generated parts:

   ```bash
   uv run python workshop/build_steps.py            # the patches: commit them if they change
   uv run python workshop/build_steps.py --branch   # the local workshop branch and tags
   ```

3. Run the preflight:

   ```bash
   uv run workshop/preflight.py --demo <SRE Orchestrator URL>
   ```

   It checks the patches, the step ladder at each tag, the branch and tags on GitHub, the
   installers, the website and the demo. Fix each ❌ with the hint on its line.
4. If GitHub has an older `workshop` branch or no tags, the preflight shows the push command.
   Ask the user, then run it. Exception: after attendees have installed, do not move the tags for a
   change to the documentation only (`git diff --stat refs/heads/workshop HEAD` shows only `.md`
   files). In each older clone, `git fetch --tags` and the installer then fail with `would clobber
   existing tag`. Tell the user that the red line for the workshop branch is then correct.
5. For the cloud demo, use the sre-agent-deploy skill. Then run the preflight again with `--demo`.
6. Rehearse one attendee: in a new folder, run the workshop installer from `INSTALL.md`, then
   `uv run workshop/step.py status`. Expect: branch `my-work`, steps 1–5 ❌.

## Change a step

1. Change the finished code on master.
2. If the change touches the code of a step, update its `solution` (and `starter`) in
   `workshop/build_steps.py`. The script fails when a solution does not match the code exactly
   once.
3. Update the step README in `workshop/steps/0N-*/`, and check that `uv run scripts/build_docs.py`
   passes.
4. Run `uv run python workshop/build_steps.py`, commit, then `--branch`, then the preflight.

## On the day

* Timeline and talking points: `workshop/FACILITATOR.md`.
* To see where an attendee is: `uv run workshop/step.py status` on their computer.
* An attendee is behind: `uv run workshop/step.py goto N` (it saves their work first).
* Common problems and fixes: `workshop/FACILITATOR.md` and `INSTALL.md` ("Problems and fixes").

## After the event

1. Remove the cloud demo: `./cleanup.sh` (ask first).
2. Tell the user to delete or rotate the Gemini API keys that attendees used.
3. Keep the branch, the tags and the website: attendees use them at home.
