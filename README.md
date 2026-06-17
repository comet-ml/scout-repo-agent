# Scout 🦉

Scout is a GitHub Action that triages new issues using Anthropic. When an issue is opened, Scout:

1. Searches for similar issues and existing workarounds
2. Explores the source code to find where the problem lives
3. Posts a structured comment with a solution, code investigation, and next steps
4. Escalates complex design issues by applying a configurable label

Activity is traced to [Opik](https://opik.com) for observability. Viewers can rate each response with a 👍/👎 reaction, which is synced back to Opik as human feedback — see [Response feedback](#response-feedback).

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
| `OPIK_API_KEY` | **yes** | Opik API key. Opik is required — Scout sources its system prompt from Opik and traces every run there. |
| `OPIK_WORKSPACE` | **yes** | Opik workspace name |
| `SCOUT_FEEDBACK_SINCE_DAYS` | no | Feedback sync only: how many days back to scan issues for 👍/👎 reactions (default: `7`) |
| `ISSUE_NUMBER` | no | Override issue number (auto-detected from event payload) |
| `SCOUT_MODEL` | no | Anthropic model ID (default: `claude-sonnet-4-6`) |
| `SCOUT_MAX_TOKENS` | no | Max response tokens (default: `8096`) |
| `SCOUT_SYSTEM_PROMPT` | no | Override the system prompt inline. Supports `$repo_owner`, `$repo_name`, `$escalation_tag` placeholders. |
| `SCOUT_PROMPT_FILE` | no | Path to a file containing the system prompt (same placeholders supported). Takes effect only when `SCOUT_SYSTEM_PROMPT` is not set. |
| `SCOUT_OPIK_PROMPT_NAME` | no | Name of the Opik-managed prompt Scout uses as its system prompt (default: `scout-system-prompt`). If no prompt by this name exists in the project, Scout auto-creates it from the built-in base prompt on first run. The Opik body is then used verbatim — no variable substitution — so edit it in the Opik UI to change behavior. **For the GitHub Action, set via the `opik_prompt_name` action input rather than `env:` — see the Opik example below.** |
| `SCOUT_OPIK_PROMPT_VERSION` | no | Pin a specific Opik prompt version (e.g. `v3`). Defaults to the latest version. **For the GitHub Action, set via the `opik_prompt_version` action input.** |

## Customizing the system prompt

Scout **always sources its system prompt from Opik** (Opik is required). On the first run for a project, if no prompt named `SCOUT_OPIK_PROMPT_NAME` (default `scout-system-prompt`) exists in the project, Scout creates it from a local **base prompt** and then uses the Opik copy verbatim on every run.

The base prompt — used only to seed Opik that first time — is resolved from `SCOUT_SYSTEM_PROMPT` (inline) > `SCOUT_PROMPT_FILE` (path to a file) > the built-in default. These three placeholders are substituted **before** the text is stored in Opik, so the stored prompt is fully resolved (no `$`-variables remain):

| Placeholder | Value |
|---|---|
| `$repo_owner` | Repository owner login |
| `$repo_name` | Repository name |
| `$escalation_tag` | Value of `SCOUT_ESCALATION_TAG` |

> Once the Opik prompt exists, it is the source of truth — changing `SCOUT_SYSTEM_PROMPT` / `SCOUT_PROMPT_FILE` no longer affects an already-seeded prompt. To change Scout's behavior after bootstrap, edit the prompt in the Opik UI (see [Using an Opik-managed prompt](#using-an-opik-managed-prompt)).

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

Scout's system prompt lives in [Opik](https://www.comet.com/opik) as a versioned artifact you can evaluate with Opik's Test Suite and improve with the Opik prompt optimizer, without redeploying the action. **This is the default and only path** — Scout bootstraps the prompt automatically (see [Customizing the system prompt](#customizing-the-system-prompt)), so you don't have to create it by hand.

**Bootstrap (automatic):** the first run with no existing prompt creates `scout-system-prompt` (version `v1`) from the base prompt. Nothing to do.

**Pre-create it (optional):** to control the body up front, create a prompt in the Opik UI before the first run. Scout uses it verbatim, so write any repo-specific values (owner, repo name, escalation tag) directly into the text. Use the same name you pass as `opik_prompt_name` (default `scout-system-prompt`).

**Reference a specific name/version from the workflow:**

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

**Seed precedence (first run only):** `SCOUT_SYSTEM_PROMPT` > `SCOUT_PROMPT_FILE` > built-in default. Once the Opik prompt exists, Opik is the source of truth. If a fetch fails transiently (e.g. network error), Scout logs a warning and falls back to the local base prompt for that run so triage still completes.

**Iterating:** edit the prompt in Opik to publish a new version. Without `SCOUT_OPIK_PROMPT_VERSION` set, the next Scout run picks it up automatically; with a pinned version, the run continues to use that version until you bump the value.

## Response feedback

Anyone viewing an issue can rate Scout's triage comment by adding a 👍 or 👎 **reaction** to it on GitHub. Those reactions are recorded in Opik as a human feedback score named `user_feedback` on the comment's trace:

- **1.0** = all 👍, **0.0** = all 👎, otherwise the ratio `👍 / (👍 + 👎)` (e.g. 3 👍 and 1 👎 → `0.75`). Reactions other than 👍/👎 are ignored.
- The score carries a `reason` that attributes the votes by GitHub login, e.g. `👍 2 (alice, bob) / 👎 1 (carol) from GitHub`, so you can see *who* reacted in the Opik UI alongside the trace.

**How it works.** GitHub fires no event when a reaction is added, so a scheduled workflow polls recent issues every 30 minutes, reads the reaction counts on Scout's comments, and upserts the score. Each Scout comment carries a hidden marker (`<!-- scout-feedback trace_id=… -->`) that maps it back to its Opik trace. The sync is idempotent — re-running simply recomputes the score from current reactions — so feedback lands in Opik within one cron interval and self-corrects as votes change.

**Enabling it.** The feedback sync ships as a second action published from this repo, `comet-ml/scout-repo-agent/actions/feedback`, alongside the triage action. Add a scheduled workflow to the repo that runs Scout — it reuses the same `OPIK_API_KEY` secret and `OPIK_WORKSPACE` value as the triage action:

```yaml
name: Scout Feedback Sync

on:
  schedule:
    - cron: '*/30 * * * *'  # every 30 minutes
  workflow_dispatch:
    inputs:
      since_days:
        description: How many days back to scan issues for reactions
        required: false
        default: '7'
        type: string

concurrency:
  group: scout-feedback-sync
  cancel-in-progress: false

jobs:
  sync-feedback:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      issues: read
      contents: read
    steps:
      - name: Sync reactions to Opik
        uses: comet-ml/scout-repo-agent/actions/feedback@main
        with:
          github_token: ${{ github.token }}
          since_days: ${{ github.event.inputs.since_days || '7' }}
        env:
          OPIK_API_KEY: ${{ secrets.OPIK_API_KEY }}
          OPIK_WORKSPACE: ${{ vars.OPIK_WORKSPACE }}
```

Trigger it manually from the Actions tab for an immediate sync.

> **Scan window.** GitHub does not bump an issue's `updated_at` when a reaction is added, so the sync only re-checks issues with other activity within `SCOUT_FEEDBACK_SINCE_DAYS` (default 7). Reactions on otherwise-quiet older issues may be missed — run the workflow manually with a larger `since_days` to backfill. Because the upsert is idempotent, re-syncing is always safe.

## Testing

Use the manual trigger workflow in this repo's Actions tab (`Test Scout (Manual)`) to run Scout against a specific issue number before enabling the automatic trigger.

## Evaluation

Scout includes two eval flows for measuring and regressing triage quality:

| Flow | Script | What it measures |
|---|---|---|
| **Offline eval** | `evals/run_offline_eval.py` | Bulk quality across real issues; scored by `UsefulnessMetric` |
| **Test suite** | `evals/run_test_suite.py` | Regression against specific scenarios; LLM-judged per-item assertions |

Both use real GitHub issues fetched via `evals/utils/fetch_github_issues.py` and stored as Opik datasets. See [`evals/README.md`](evals/README.md) for the full setup and usage guide.

## Local development

The code is an installable package under `src/scout/`. Install it (with dev extras) in editable mode:

```bash
pip install -e ".[dev]"

# Copy and fill in the template
cp .env.example .env

# Run triage locally (console script registered by the install).
# Equivalent to `python -m scout.triage`.
scout-triage
```

Run the unit tests and linters with `pytest`, `ruff check .`, and `mypy src/scout`.

See `.env.example` for all available environment variables.
