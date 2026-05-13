# Scout

Scout is a GitHub Action that triages new issues using Claude. When an issue is opened, Scout:

1. Searches for similar issues and existing workarounds
2. Explores the source code to find where the problem lives
3. Posts a structured comment with a solution, code investigation, and next steps
4. Escalates complex design issues by applying a configurable label

Activity is traced to [Opik](https://opik.com) for observability.

## Setup

### 1. Create the escalation label

In the target repository, create a label matching your `SCOUT_ESCALATION_TAG` value (e.g. `Escalated request`). Scout will apply this label to issues that require major design decisions.

### 2. Configure secrets and variables

In your repository settings, add:

**Secrets** (`Settings → Secrets and variables → Actions → Secrets`):
| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SCOUT_APP_ID` | GitHub App ID |
| `SCOUT_APP_PRIVATE_KEY` | GitHub App private key (PEM file contents, or path to file on runner) |
| `OPIK_API_KEY` | Opik API key |

**Variables** (`Settings → Secrets and variables → Actions → Variables`):
| Variable | Description |
|---|---|
| `SCOUT_GITHUB_REPO_OWNER` | Repository owner (e.g. `comet-ml`) |
| `SCOUT_GITHUB_REPO_NAME` | Repository name (e.g. `opik`) |
| `SCOUT_ESCALATION_TAG` | Label name for escalated issues (e.g. `Escalated request`) |
| `OPIK_WORKSPACE` | Opik workspace name |

### 3. Add the workflow

Create `.github/workflows/scout.yml` in your target repository:

```yaml
name: Scout Issue Triage

on:
  issues:
    types: [opened]

# One Scout run per issue at a time
concurrency:
  group: scout-issue-${{ github.event.issue.number }}
  cancel-in-progress: false

jobs:
  triage:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      issues: write
      contents: read

    steps:
      - name: Run Scout
        uses: comet-ml/scout-repo-agent@main
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
        env:
          SCOUT_APP_ID: ${{ secrets.SCOUT_APP_ID }}
          SCOUT_APP_PRIVATE_KEY: ${{ secrets.SCOUT_APP_PRIVATE_KEY }}
          SCOUT_ESCALATION_TAG: ${{ vars.SCOUT_ESCALATION_TAG }}
          SCOUT_GITHUB_REPO_OWNER: ${{ vars.SCOUT_GITHUB_REPO_OWNER }}
          SCOUT_GITHUB_REPO_NAME: ${{ vars.SCOUT_GITHUB_REPO_NAME }}
          OPIK_API_KEY: ${{ secrets.OPIK_API_KEY }}
          OPIK_WORKSPACE: ${{ vars.OPIK_WORKSPACE }}
```

## GitHub App requirements

The GitHub App must have these permissions:
- **Issues**: Read & Write (to read issues and post comments)
- **Contents**: Read (to read source files)

## Configuration reference

| Env var | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Anthropic API key |
| `SCOUT_APP_ID` | yes | GitHub App ID |
| `SCOUT_APP_PRIVATE_KEY` | yes | PEM file path or raw PEM contents |
| `SCOUT_GITHUB_REPO_OWNER` | yes | Repo owner login |
| `SCOUT_GITHUB_REPO_NAME` | yes | Repo name |
| `SCOUT_ESCALATION_TAG` | no | Label for escalated issues (default: `Escalated request`) |
| `OPIK_API_KEY` | no | Opik API key for tracing |
| `OPIK_WORKSPACE` | no | Opik workspace name |
| `ISSUE_NUMBER` | no | Override issue number (auto-detected from event payload) |
| `SCOUT_MODEL` | no | Claude model (default: `claude-opus-4-5`) |
| `SCOUT_MAX_TOKENS` | no | Max response tokens (default: `8096`) |

## Testing

Use the manual trigger workflow in this repo's Actions tab (`Test Scout (Manual)`) to run Scout against a specific issue number before enabling the automatic trigger.

## Local development

```bash
pip install -r requirements.txt

# Copy and fill in the template
cp .env.example .env

python scout.py
```

`.env.example`:
```
ANTHROPIC_API_KEY=
SCOUT_APP_ID=
SCOUT_APP_PRIVATE_KEY=PATH/github_app_key.pem
SCOUT_ESCALATION_TAG=Escalated request
SCOUT_GITHUB_REPO_OWNER=owner
SCOUT_GITHUB_REPO_NAME=name
ISSUE_NUMBER=123
OPIK_WORKSPACE=comet-all
OPIK_API_KEY=
```
