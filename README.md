# Scout 🦉

Scout is a GitHub Action that triages new issues using Anthropic. When an issue is opened, Scout:

1. Searches for similar issues and existing workarounds
2. Explores the source code to find where the problem lives
3. Posts a structured comment with a solution, code investigation, and next steps
4. Escalates complex design issues by applying a configurable label

Activity is traced to [Opik](https://opik.com) for observability.

## Setup

### 1. Configure secrets and variables

In your repository settings, add:

**Secrets** (`Settings → Secrets and variables → Actions → Secrets`):
| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPIK_API_KEY` | Opik API key |

> The `github.token` built-in is used for GitHub access — no personal access token or GitHub App required. Comments will appear as `github-actions[bot]`.

**Variables** (`Settings → Secrets and variables → Actions → Variables`):
| Variable | Description |
|---|---|
| `SCOUT_GITHUB_REPO_OWNER` | Repository owner (e.g. `comet-ml`) |
| `SCOUT_GITHUB_REPO_NAME` | Repository name (e.g. `opik`) |
| `SCOUT_ESCALATION_TAG` | Label name for escalated issues (e.g. `Escalated request`) |
| `OPIK_WORKSPACE` | Opik workspace name |

### 2. Add the workflow

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
          github_token: ${{ github.token }}
        env:
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
| `GITHUB_TOKEN` | yes | GitHub token — pass `${{ github.token }}` via the action input |
| `SCOUT_GITHUB_REPO_OWNER` | yes | Repo owner login |
| `SCOUT_GITHUB_REPO_NAME` | yes | Repo name |
| `SCOUT_ESCALATION_TAG` | no | Label for escalated issues (default: `Escalated request`) |
| `OPIK_API_KEY` | no | Opik API key for tracing |
| `OPIK_WORKSPACE` | no | Opik workspace name |
| `ISSUE_NUMBER` | no | Override issue number (auto-detected from event payload) |
| `SCOUT_MODEL` | no | Anthropic model ID (default: `claude-sonnet-4-6`) |
| `SCOUT_MAX_TOKENS` | no | Max response tokens (default: `8096`) |
| `SCOUT_SYSTEM_PROMPT` | no | Override the system prompt inline. Supports `$repo_owner`, `$repo_name`, `$escalation_tag` placeholders. |
| `SCOUT_PROMPT_FILE` | no | Path to a file containing the system prompt (same placeholders supported). Takes effect only when `SCOUT_SYSTEM_PROMPT` is not set. |

## Customizing the system prompt

Scout's system prompt can be replaced via `SCOUT_SYSTEM_PROMPT` (inline) or `SCOUT_PROMPT_FILE` (path to a file). Both support three placeholders that are substituted at runtime:

| Placeholder | Value |
|---|---|
| `$repo_owner` | Repository owner login |
| `$repo_name` | Repository name |
| `$escalation_tag` | Value of `SCOUT_ESCALATION_TAG` |

**Example: prompt file in the workflow**

Create `.github/scout-prompt.txt` in your target repository:

```
You are Scout 🦉, a triage agent for $repo_owner/$repo_name.

For each new issue:
1. Search for duplicates using search_issues.
2. Identify the relevant source files with list_directory and get_file_contents.
3. Reply with a short summary, the affected file(s), and a suggested fix.

If the fix requires a breaking API change, call apply_label("$escalation_tag") before replying.

Keep responses concise and technical. Do not use filler phrases.
```

Then pass it to Scout in your workflow:

```yaml
      - name: Run Scout
        uses: comet-ml/scout-repo-agent@main
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ github.token }}
        env:
          SCOUT_ESCALATION_TAG: ${{ vars.SCOUT_ESCALATION_TAG }}
          SCOUT_GITHUB_REPO_OWNER: ${{ vars.SCOUT_GITHUB_REPO_OWNER }}
          SCOUT_GITHUB_REPO_NAME: ${{ vars.SCOUT_GITHUB_REPO_NAME }}
          OPIK_API_KEY: ${{ secrets.OPIK_API_KEY }}
          OPIK_WORKSPACE: ${{ vars.OPIK_WORKSPACE }}
          SCOUT_PROMPT_FILE: ${{ github.workspace }}/.github/scout-prompt.txt
```

**Example: inline prompt via `SCOUT_SYSTEM_PROMPT`**

For shorter prompts you can set the value directly as a GitHub Actions variable (`Settings → Secrets and variables → Actions → Variables`):

```yaml
      - name: Run Scout
        uses: comet-ml/scout-repo-agent@main
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ github.token }}
        env:
          SCOUT_ESCALATION_TAG: ${{ vars.SCOUT_ESCALATION_TAG }}
          SCOUT_GITHUB_REPO_OWNER: ${{ vars.SCOUT_GITHUB_REPO_OWNER }}
          SCOUT_GITHUB_REPO_NAME: ${{ vars.SCOUT_GITHUB_REPO_NAME }}
          OPIK_API_KEY: ${{ secrets.OPIK_API_KEY }}
          OPIK_WORKSPACE: ${{ vars.OPIK_WORKSPACE }}
          SCOUT_SYSTEM_PROMPT: ${{ vars.SCOUT_SYSTEM_PROMPT }}
```

> When both `SCOUT_SYSTEM_PROMPT` and `SCOUT_PROMPT_FILE` are set, `SCOUT_SYSTEM_PROMPT` takes precedence.

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
GITHUB_TOKEN=github_pat_...
SCOUT_ESCALATION_TAG=Escalated request
SCOUT_GITHUB_REPO_OWNER=owner
SCOUT_GITHUB_REPO_NAME=name
ISSUE_NUMBER=123
OPIK_WORKSPACE=comet-all
OPIK_API_KEY=
# Optional: override the system prompt (supports $repo_owner, $repo_name, $escalation_tag)
# SCOUT_SYSTEM_PROMPT=
# SCOUT_PROMPT_FILE=
```
