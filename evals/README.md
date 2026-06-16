# Offline evaluation

Scout includes a reproducible eval harness that runs the agent against a snapshot dataset stored in Opik, so you can measure quality changes without hitting live GitHub issues on every run.

## How it works

Each dataset item uses a `scenario`/`spec`/`target_issue` shape. The `spec` is handed to `providers/scenarios.build()`, which constructs a `GitHubSimulator` — issues and writes are always simulated; file reads can be fully simulated (include a `files` key in the spec) or delegated to real GitHub (omit `files`, requires `GITHUB_TOKEN`).

## Setup

Add these to your `.env` alongside the standard Scout config (see `.env.example` for all variables):

| Var | Default | Description |
|---|---|---|
| `SCOUT_GITHUB_DATASET_NAME` | `scout-triage-inputs` | Opik dataset of real GitHub issues |
| `SCOUT_STARTER_DATASET_NAME` | `scout-starter-scenarios` | Opik dataset of synthetic starter scenarios |
| `SCOUT_EVAL_OPIK_PROJECT` | `scout-eval` | Opik project for eval traces |
| `SCOUT_EXPERIMENT_NAME` | `scout-offline-eval` | Experiment name prefix (timestamp appended per run) |
| `SCOUT_GITHUB_REPO_OWNER` | — | Required for real-GitHub file mode |
| `SCOUT_GITHUB_REPO_NAME` | — | Required for real-GitHub file mode |

## Datasets

Two separate Opik datasets are used:

| Dataset | Contents | Seeded from |
|---|---|---|
| `scout-triage-inputs` | Real issues from the GitHub repo | `fetch_github_issues.py` → `seed_dataset.py --from-github` |
| `scout-starter-scenarios` | Synthetic fully-simulated scenarios | `seed_dataset.py --from-starter` |

## Dataset item format

Every item in an Opik dataset must follow this shape:

```json
{
  "data": {
    "scenario": "default",
    "spec": {
      "owner": "my-org",
      "name": "my-repo",
      "readme": "...",
      "files": {"src/foo.py": "..."},
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

Omit `files` (and `readme`) inside `spec` to use real-GitHub mode — `list_directory`, `get_file_contents`, and `fetch_readme` will be fetched from the live repo via `GITHUB_TOKEN`.

## Step 1 — Fetch issues from GitHub

`evals/utils/fetch_github_issues.py` fetches real issues from any GitHub repo and saves them as JSON. It uses the GitHub search API with `is:issue` to exclude pull requests.

```bash
python evals/utils/fetch_github_issues.py --count 30 --state all --out github_issues.json
```

Options:
- `--repo owner/name` — override the repo (defaults to `SCOUT_GITHUB_REPO_OWNER`/`SCOUT_GITHUB_REPO_NAME`)
- `--count N` — number of issues to fetch (default: 10)
- `--state open|closed|all` — issue state filter (default: open)
- `--out FILE` — output path (default: `github_issues.json`)

> JSON output files are gitignored — don't commit them.

## Step 2 — Seed the datasets

`evals/utils/seed_dataset.py` inserts items into Opik datasets. The two sources seed separate datasets:

**Real GitHub issues** → `scout-triage-inputs`:

```bash
python evals/utils/seed_dataset.py --from-github github_issues.json
```

**Synthetic starter scenarios** → `scout-starter-scenarios`:

```bash
python evals/utils/seed_dataset.py --from-starter
```

**Both at once:**

```bash
python evals/utils/seed_dataset.py --from-github github_issues.json --from-starter
```

Each script creates the dataset if it doesn't exist, or appends to an existing one. To start clean, delete the dataset in the Opik UI before re-seeding.

## Step 3 — Run the eval

```bash
python evals/run_offline_eval.py
```

By default this runs against `scout-triage-inputs`. Set `SCOUT_GITHUB_DATASET_NAME` in `.env` to target a different dataset.

Results and traces are logged to Opik under the project set in `SCOUT_EVAL_OPIK_PROJECT`. Each run gets a unique timestamped experiment name so results are easy to compare across runs.

The task returns `output` (the comment Scout posted), `final_labels`, `applied_labels`, and `search_queries`, so scoring metrics can reference side-effect state, not just the comment text.
