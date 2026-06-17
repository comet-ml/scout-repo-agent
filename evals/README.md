# Evals

Scout has two evaluation flows, both backed by Opik:

| Flow | Runner | What it measures |
|---|---|---|
| **Offline eval** | `evals/run_offline_eval.py` | Bulk quality across many real issues; scored by `UsefulnessMetric` (Claude Sonnet) |
| **Test suite** | `evals/run_test_suite.py` | Regression against specific scenarios; LLM-judged per-item assertions (Claude Haiku) |

---

## Prerequisites

The following must be set in your `.env` before running anything (see `.env.example` for all variables):

```bash
ANTHROPIC_API_KEY=       # required — Scout agent and LLM judge both use Claude
GITHUB_TOKEN=            # required — fetching issues and real-GitHub file mode during evals
OPIK_API_KEY=            # required — logging traces and reading datasets
OPIK_WORKSPACE=          # required — your Opik workspace name
OPIK_ENVIRONMENT=dev     # dev (local + offline evals), test (test suite — auto-set), staging (UAT), prod (GitHub Action)
SCOUT_GITHUB_REPO_OWNER= # required — repo to fetch issues from
SCOUT_GITHUB_REPO_NAME=  # required — repo to fetch issues from
SCOUT_EVAL_OPIK_PROJECT= # optional — defaults to scout:{SCOUT_GITHUB_REPO_OWNER}/{SCOUT_GITHUB_REPO_NAME}
```

---

## End-to-end flow

Both eval flows share the same pipeline:

```
fetch_github_issues.py  →  seed_dataset.py / seed_test_suite.py  →  run_offline_eval.py / run_test_suite.py
       (fetch)                        (seed Opik)                            (run)
```

### Step 1 — Fetch issues from GitHub

`evals/utils/fetch_github_issues.py` fetches real issues from any GitHub repo using the search API with `is:issue` (pull requests excluded). Output is a JSON file consumed by the seed scripts.

```bash
python evals/utils/fetch_github_issues.py --count 30 --state all --out github_issues.json
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--repo owner/name` | env vars | Override the target repo |
| `--count N` | `10` | Number of issues to fetch |
| `--state open\|closed\|all` | `open` | Issue state filter |
| `--out FILE` | `github_issues.json` | Output path |

> JSON output files are gitignored — don't commit them.

### Step 2 — Seed Opik

Choose the offline dataset, the test suite, or both.

**Offline eval datasets** (two separate datasets, one command):

```bash
python evals/utils/seed_dataset.py --from-github github_issues.json --from-starter
```

| Flag | Seeds dataset | Mode |
|---|---|---|
| `--from-github FILE` | `scout-triage-inputs` | Real issues — file reads hit live GitHub during the eval |
| `--from-starter` | `scout-starter-scenarios` | Synthetic, fully simulated — no network required during eval |

**Test suite** (`scout-triage-regression`):

```bash
python evals/utils/seed_test_suite.py --from-github github_issues.json --from-starter
```

| Flag | Items seeded | Assertions |
|---|---|---|
| `--from-github FILE` | Real GitHub issues | None — add via Opik UI after reviewing Scout's output |
| `--from-starter` | Synthetic starter scenarios | Predefined per-item assertions included |

All seed scripts create the dataset/suite if it doesn't exist, or append to an existing one. Delete in the Opik UI before re-seeding to start clean.

### Step 3 — Run

**Offline eval** — runs against `SCOUT_GITHUB_DATASET_NAME` (default: `scout-triage-inputs`):

```bash
python evals/run_offline_eval.py
```

Each run gets a unique timestamped experiment name. Results and traces are logged to `SCOUT_EVAL_OPIK_PROJECT`.

**Test suite** — runs against the hardcoded `scout-triage-regression` suite:

```bash
python evals/run_test_suite.py
```

Pass rate is printed on completion and visible in the Opik dashboard.

> **Environment convention:** `run_test_suite.py` automatically defaults to `OPIK_ENVIRONMENT=test` — no manual setup needed. Offline eval runs use `dev` from `.env`. Override either by setting `OPIK_ENVIRONMENT` in the shell before running (e.g. `OPIK_ENVIRONMENT=staging` for UAT). The GitHub Action sets `prod`.

---

## Simulation modes

Both flows use a `GitHubSimulator` that intercepts the agent's GitHub tool calls. The mode depends on whether `files` is present in the spec:

| Mode | `files` in spec | File reads | Issue search | Network needed |
|---|---|---|---|---|
| **Simulated** | Yes (or using starters) | From spec | From spec | No |
| **Real-GitHub** | No (fetched issues) | Live GitHub API | From spec (target only) | Yes — `GITHUB_TOKEN` |

> Real-GitHub mode fetches file contents at eval time, not at seed time. `GITHUB_TOKEN` must be valid when running the eval, not just when seeding.

---

## Dataset item format

Both flows consume items in this shape:

```json
{
  "data": {
    "scenario": "default",
    "spec": {
      "owner": "my-org",
      "name": "my-repo",
      "issues": [
        {
          "number": 42,
          "title": "...",
          "body": "...",
          "state": "open",
          "author": "alice",
          "labels": [],
          "comments": []
        }
      ]
    },
    "target_issue": 42
  }
}
```

Include `"files": {"src/foo.py": "..."}` and `"readme": "..."` in `spec` to use fully simulated mode. Omit them for real-GitHub mode.
