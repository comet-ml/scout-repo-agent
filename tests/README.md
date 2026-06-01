# Offline evaluation

Scout includes a reproducible eval harness that runs the agent against a snapshot dataset stored in Opik, so you can measure quality changes without hitting live GitHub issues on every run.

## How it works

Each eval item uses a `_SnapshotProvider` — the issue itself is served from the stored dataset snapshot (reproducible), while file reads (`list_directory`, `get_file_contents`, `fetch_readme`, `search_issues`) go to the real GitHub repo so Scout explores real code.

## Setup

Add these to your `.env` alongside the standard Scout config:

| Var | Default | Description |
|---|---|---|
| `SCOUT_OFFLINE_DATASET_NAME` | `scout-test-issues` | Opik dataset name |
| `SCOUT_OFFLINE_OPIK_PROJECT` | `scout:comet-ml/scout-test-repo` | Opik project for eval traces |
| `SCOUT_EXPERIMENT_NAME` | `scout-offline-eval` | Experiment name prefix (timestamp appended per run) |

## Step 1 — Fetch issues into a CSV

`tests/utils/fetch_test_issues.py` pulls issues with comments from your target repo:

```bash
python tests/utils/fetch_test_issues.py --count 10 --state open
# or target a specific repo:
python tests/utils/fetch_test_issues.py --repo comet-ml/scout-test-repo --count 10
```

Output columns: `number`, `title`, `state`, `author`, `labels`, `body`, `comments_json`.

> CSV files are gitignored — don't commit them.

## Step 2 — Upload the CSV to an Opik dataset

```python
import csv
import opik

client = opik.Opik()
dataset = client.get_or_create_dataset(
    name="scout-test-issues",
    project_name="scout:comet-ml/scout-test-repo",
)

with open("test_issues.csv", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

dataset.insert(rows)
```

Each CSV row becomes one dataset item. The flat column fields are the format `run_offline_eval.py` expects.

## Step 3 — Run the eval

```bash
python tests/run_offline_eval.py
```

Results and traces are logged to Opik under the project set in `SCOUT_OFFLINE_OPIK_PROJECT`. Each run gets a unique timestamped experiment name so results are easy to compare across runs.