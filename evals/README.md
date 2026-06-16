# Offline evaluation

Scout includes a reproducible eval harness that runs the agent against a snapshot dataset stored in Opik, so you can measure quality changes without hitting live GitHub issues on every run.

## How it works

Each dataset item uses the same `scenario`/`spec`/`target_issue` shape as the Test Suite eval (`scout_eval.py`). The `spec` is handed to `providers/scenarios.build()`, which constructs a `GitHubSimulator` — issues and writes are always simulated; file reads can be fully simulated (include a `files` key in the spec) or delegated to real GitHub (omit `files`, requires `GITHUB_TOKEN`).

## Setup

Add these to your `.env` alongside the standard Scout config:

| Var | Default | Description |
|---|---|---|
| `SCOUT_OFFLINE_DATASET_NAME` | `scout-test-issues` | Opik dataset name |
| `SCOUT_OFFLINE_OPIK_PROJECT` | `scout:comet-ml/scout-test-repo` | Opik project for eval traces |
| `SCOUT_EXPERIMENT_NAME` | `scout-offline-eval` | Experiment name prefix (timestamp appended per run) |
| `SCOUT_GITHUB_REPO_OWNER` | — | Required when seeding from a CSV (real-GitHub file mode) |
| `SCOUT_GITHUB_REPO_NAME` | — | Required when seeding from a CSV (real-GitHub file mode) |

## Dataset item format

Every item in the Opik dataset must follow this shape:

```json
{
  "description": "my-scenario-name",
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

`description` is shown as the row label in the Opik UI. The `data` field is what the eval task consumes — `scenario`, `spec`, and `target_issue`. Omit `files` (and `readme`) inside `spec` to use real-GitHub mode — `list_directory`, `get_file_contents`, and `fetch_readme` will be fetched from the live repo via `GITHUB_TOKEN`.

## Step 1 — Seed the dataset

`evals/utils/seed_offline_dataset.py` inserts items into an Opik dataset. Two sources can be combined in one run:

**From a CSV** (issues captured by `fetch_test_issues.py` — real-GitHub file mode):

```bash
# First fetch issues into a CSV:
python evals/utils/fetch_test_issues.py --count 10 --state open

# Then seed the dataset:
python evals/utils/seed_offline_dataset.py --from-csv test_issues.csv
```

**From the starter scenarios** (fully simulated, no network required):

```bash
python evals/utils/seed_offline_dataset.py --from-starter
```

**Both at once:**

```bash
python evals/utils/seed_offline_dataset.py --from-csv test_issues.csv --from-starter
```

The script creates the dataset if it doesn't exist, or appends to an existing one. To start clean, delete the dataset in the Opik UI before re-seeding.

> CSV files are gitignored — don't commit them.

## Step 2 — Run the eval

```bash
python evals/run_offline_eval.py
```

Results and traces are logged to Opik under the project set in `SCOUT_OFFLINE_OPIK_PROJECT`. Each run gets a unique timestamped experiment name so results are easy to compare across runs.

The task returns `output` (the comment Scout posted), `final_labels`, `applied_labels`, and `search_queries` — the same shape as `scout_eval.py` — so scoring metrics and assertions can reference side-effect state, not just the comment text.