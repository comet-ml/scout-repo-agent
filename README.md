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
  workflow_dispatch:
    inputs:
      issue_number:
        description: Issue number to triage
        required: true
        type: number

# One Scout run per issue at a time
concurrency:
  group: scout-issue-${{ github.event.issue.number || github.event.inputs.issue_number }}
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
          ISSUE_NUMBER: ${{ github.event.issue.number || github.event.inputs.issue_number }}
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
| `SCOUT_OPIK_PROMPT_NAME` | no | Name of an Opik-managed prompt to use as the system prompt. Requires `OPIK_API_KEY` and `OPIK_WORKSPACE`. The Opik body is used verbatim — no variable substitution is performed, so write repo-specific values (owner/name, escalation tag) directly into the prompt text. **For the GitHub Action, set via the `opik_prompt_name` action input rather than `env:` — see the Opik example below.** |
| `SCOUT_OPIK_PROMPT_VERSION` | no | Pin a specific Opik prompt version (e.g. `v3`). Defaults to the latest version. **For the GitHub Action, set via the `opik_prompt_version` action input.** |

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

### Using an Opik-managed prompt

You can store Scout's system prompt in [Opik](https://www.comet.com/opik) and reference it by name. This turns the prompt into a versioned artifact you can evaluate with Opik's Test Suite and improve with the Opik prompt optimizer, without having to redeploy the action.

**Set up the prompt in Opik:**

1. In the Opik UI, create a new prompt (e.g. named `scout-system-prompt`).
2. Paste your prompt body. Scout uses it verbatim, so write any repo-specific values (owner, repo name, escalation tag) directly into the text rather than using template variables.
3. Save. The first save creates version `v1`.

**Reference it from the workflow:**

```yaml
      - name: Run Scout
        uses: comet-ml/scout-repo-agent@main
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ github.token }}
          opik_prompt_name: scout-system-prompt
          # opik_prompt_version: v3  # optional; omit to use the latest version
        env:
          SCOUT_ESCALATION_TAG: ${{ vars.SCOUT_ESCALATION_TAG }}
          SCOUT_GITHUB_REPO_OWNER: ${{ vars.SCOUT_GITHUB_REPO_OWNER }}
          SCOUT_GITHUB_REPO_NAME: ${{ vars.SCOUT_GITHUB_REPO_NAME }}
          OPIK_API_KEY: ${{ secrets.OPIK_API_KEY }}
          OPIK_WORKSPACE: ${{ vars.OPIK_WORKSPACE }}
          ISSUE_NUMBER: ${{ github.event.issue.number || github.event.inputs.issue_number }}
```

**Precedence:** Opik > `SCOUT_SYSTEM_PROMPT` > `SCOUT_PROMPT_FILE` > built-in default. If `SCOUT_OPIK_PROMPT_NAME` is set but the fetch fails (network error, prompt not found, Opik not configured), Scout logs a warning and falls back to the next source so triage still runs.

**Iterating:** edit the prompt in Opik to publish a new version. Without `SCOUT_OPIK_PROMPT_VERSION` set, the next Scout run picks it up automatically; with a pinned version, the run continues to use that version until you bump the value.

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
# Optional: fetch the system prompt from Opik (body used verbatim — no variable substitution)
# SCOUT_OPIK_PROMPT_NAME=
# SCOUT_OPIK_PROMPT_VERSION=
```
