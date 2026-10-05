---
name: sre-agent-deploy
description: Deploys this repository (the SRE agent) to Google Cloud Run with bootstrap.sh and deploy.sh, verifies the deployment and removes it with cleanup.sh. Use when the user asks to deploy, redeploy, test in the cloud or tear down the SRE agent demo. For a local setup, use the sre-agent-setup skill.
---

# Deploy the SRE agent to Google Cloud

The deployment creates billable resources in the user's GCP project: four Cloud Run services, a
Cloud Run job, an Artifact Registry repository, a Firestore database, a secret and four service
accounts. Get the user's approval at each step that is marked **(approval)**.

## Rules

* Do not ask for the `GEMINI_API_KEY` value, and do not show it or write it yourself.
  `bootstrap.sh` asks for it with hidden input and writes `.env`. `.env` is in `.gitignore`.
  Never commit it.
* `bootstrap.sh` is interactive. Ask the user to run it in their own terminal. Do not run it
  for them.
* Do not change IAM policies, billing or APIs yourself. The scripts do these changes after the
  user starts them.
* The scripts need bash. On Windows, use Google Cloud Shell (it has all tools), WSL or Git Bash.
* Run one command at a time, and read its output.

## Procedure

1. **Check the tools:** `gcloud --version` and `gcloud auth list`. If gcloud is missing, give the
   link <https://cloud.google.com/sdk/docs/install>, or suggest Google Cloud Shell.

2. **Configure the project (approval).** Ask the user to run `./bootstrap.sh` in their terminal.
   The script asks for the project ID, links billing if necessary, asks for the region and the
   Gemini API key, and writes `.env`. Wait until the user tells you that it is done.

3. **Deploy (approval).** Tell the user that the deployment takes 5 to 10 minutes and creates
   billable resources. Then run `./deploy.sh`. For a later redeploy of code changes only, run
   `./deploy.sh --skip-infra`.

4. **Verify.** The last lines of `deploy.sh` show the service URLs.
   1. Make an incident: `curl "<Target App URL>/api/gateway?trigger_error=true"`.
   2. Wait 1 to 2 minutes. Cloud Trace shows new traces after a delay.
   3. Tell the user to open `<SRE Orchestrator URL>/chat` and to ask
      "What are the latest failures?".

5. **Warn about access.** All services allow unauthenticated access, so that the demo is simple.
   Each person with a URL can use the agents and the user's Gemini quota.

6. **Clean up (approval).** After the demo, run `./cleanup.sh`. It deletes all the resources that
   `deploy.sh` made. It asks before it deletes the Firestore database and the local data.
