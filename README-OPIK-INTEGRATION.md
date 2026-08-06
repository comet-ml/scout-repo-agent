# Scout × Opik Test Suite integration

Scout's agent loop is decoupled from GitHub via a `RepositoryProvider` protocol, so a `GitHubSimulator` can stand in for the real API. This lets you evaluate the agent end-to-end against Opik Test Suite scenarios without ever touching a real repository. This document covers the why, the architecture, and how to add scenarios of your own.

## Why simulate GitHub?

Scout's production job is end-to-end: it reads an issue, searches for duplicates, navigates source code, posts a comment, and may apply a label. Evaluating that behavior against real GitHub has three problems:

1. **State drifts.** A scenario you wrote yesterday isn't the same scenario today — issues get edited, new duplicates appear, source files change. Regression results stop being comparable.
2. **Side effects.** Posting comments and applying labels on a real repo just to test the agent is not OK. A dry-run flag would solve that but adds branching code paths and still leaves the read-side state nondeterministic.
3. **Adversarial cases are hard to construct.** "A duplicate exists but only with the right search terms" or "the relevant file is in three places" require building repos that don't exist anywhere.

The fix: an in-memory simulator that behaves like GitHub at the seams Scout actually touches, plus a registry that turns JSON dataset rows into populated simulators. The Test Suite measures what's actually being evaluated — *given an issue and a repo context, does Scout produce a good triage?* — not the GitHub plumbing.

## Architecture

```
scout.triage             evals/run_eval.py
   │                          │
   ▼                          ▼
GitHubProvider           GitHubSimulator      ◄── implements ──┐
   (PyGithub)            (in-memory)                            │
                                                                │
                                                       RepositoryProvider
                                                  (scout/providers/base.py)
                                                                ▲
                                                                │
                                                          agent.run_agent
                                                       (scout/agent.py)
```

`agent.run_agent` is the single agent loop. It depends only on the `RepositoryProvider` protocol — not on PyGithub, not on any module globals. Both backends satisfy the same interface; swapping them changes nothing about the loop, the prompt, the model, or the tool dispatch.

The protocol covers everything Scout's tools (and `main()`) need from "GitHub":

```python
# src/scout/providers/base.py
class RepositoryProvider(Protocol):
    # read
    def get_issue_data(self, issue_number: int) -> dict: ...
    def search_issues(self, query: str, max_results: int) -> list[dict]: ...
    def list_directory(self, path: str) -> list[str]: ...
    def get_file_contents(self, path: str) -> str: ...
    def fetch_readme(self) -> str | None: ...
    # write
    def add_reaction(self, issue_number: int, reaction: str) -> None: ...
    def apply_label(self, issue_number: int, label_name: str) -> str: ...
    def post_comment(self, issue_number: int, body: str) -> None: ...
```

`GitHubProvider` is lifted directly from the prior module-level tools — same logic, same return shapes. `GitHubSimulator` is new.

## The simulator

`src/scout/providers/simulator.py` defines `GitHubSimulator` — a real object with mutable state, not a passive fixture. Scenarios build it up with a fluent API; the agent reads and writes against it; assertions inspect both the output text and the resulting state.

```python
sim = (
    GitHubSimulator(owner="trainer-org", name="trainer-lib")
    .add_issue(999, title="Training hangs on 4 GPUs", body="...")
    .add_issue(412, title="multi-gpu deadlock", body="fixed in dist.py",
               state="closed")
    .add_file("src/distributed/all_reduce.py", "...")
    .set_readme("# trainer-lib\n\nLightweight PyTorch trainer...")
)
```

Four properties make this more than a dict:

1. **Default search is realistic.** `search_issues` performs substring matching against title + body, not "return everything". Scenarios can probe Scout's query-formulation behavior — "the right duplicate exists, but only if Scout searches with the right terms."
2. **State is mutable and observable.** `apply_label` actually changes the issue's `labels` list. Assertions can ask state questions ("is 'Escalated-request' on issue #999 now?"), not just call-log questions ("was apply_label invoked?"). `sim.issue(999)["labels"]` returns the live state.
3. **Behavior is swappable per scenario.** `sim.set_search_handler(fn)` overrides the default matcher with a callable of `(query, max_results, issues) -> list[dict]`. Use for flaky search, pagination quirks, results-after-N-calls — anything you can express in Python.
4. **Side effects are recorded.** Every mutating call is appended to `sim.calls` as a tuple. The eval driver surfaces a filtered view of this so LLM judges can grade "did Scout apply the label?" or "what search queries did it issue?".

## Two modes: simulated vs. real-GitHub code

The `default` scenario builder picks its mode from a single signal — whether `spec["files"]` is present.

| Mode | Trigger | Code reads (`list_directory`, `get_file_contents`, `fetch_readme`) | Issues, search, writes |
|---|---|---|---|
| Simulated | `"files"` key present (may be empty `{}`) | Served from `spec["files"]` and `spec["readme"]` | Simulated |
| Real-GitHub | `"files"` key absent | Delegated to `GitHubProvider` against `spec["owner"]/spec["name"]` | Simulated |

**Use simulated mode when** the scenario needs adversarially constructed code (a buggy file with a specific shape), or when you want the regression to be hermetic.

**Use real-GitHub mode when** the scenario only cares about issue triage behavior against your actual repo's code — you don't want to embed kilobytes of source in the spec, and live code is acceptable.

Constraints in real-GitHub mode:
- `GITHUB_TOKEN` must be set to a real token (the eval-time `unused` placeholder is rejected).
- `owner`/`name` must name a reachable repo.
- A `readme` key in the spec is rejected loudly — the README is fetched from GitHub, so a stray key would be silently shadowed.
- Issues stay simulated. `target_issue` must appear in `spec["issues"]`; the simulator never reads issues from real GitHub. This keeps regression results deterministic even when real-repo state drifts.
- Writes stay simulated. `apply_label` and `post_comment` mutate the simulator, never the real repo.

Real-GitHub mode hits the network on every read, so a flaky GitHub API or rate-limit cap will surface as scenario failures. Snapshot/replay caching is a possible future addition.

Example real-GitHub spec:
```json
{
  "scenario": "default",
  "spec": {
    "owner": "comet-ml",
    "name": "opik",
    "issues": [
      {"number": 999, "title": "Trainer hangs on multi-gpu",
       "body": "fit() deadlocks on the all-reduce step",
       "author": "u1", "state": "open", "labels": []}
    ]
  },
  "target_issue": 999
}
```

## Scenarios — bridging JSON to the simulator

Opik dataset rows are JSON; simulator behavior is Python. `src/scout/providers/scenarios.py` reconciles them with a small registry:

```python
SCENARIO_BUILDERS: dict[str, Callable[[dict], GitHubSimulator]] = {}

def register(name: str):
    def deco(fn):
        SCENARIO_BUILDERS[name] = fn
        return fn
    return deco

@register("default")
def _default(spec: dict) -> GitHubSimulator:
    upstream = None
    if "files" not in spec:                      # real-GitHub mode
        # ...validate GITHUB_TOKEN, reject stray 'readme'...
        upstream = GitHubProvider(token, owner, name)
    sim = GitHubSimulator(owner, name, upstream=upstream)
    if "readme" in spec:
        sim.set_readme(spec["readme"])
    for path, content in spec.get("files", {}).items():
        sim.add_file(path, content)
    for issue in spec.get("issues", []):
        sim.add_issue(**issue)
    return sim

@register("search-rate-limited")
def _search_rate_limited(spec: dict) -> GitHubSimulator:
    """Same data as default, but search returns [] after the second call."""
    sim = _default(spec)
    calls = [0]
    def handler(q, n, issues):
        calls[0] += 1
        return [] if calls[0] > 2 else GitHubSimulator._default_search(q, n, issues)
    sim.set_search_handler(handler)
    return sim
```

Most scenarios use `"default"`. The long tail registers a Python builder by name and references it from the JSON. The dataset schema doesn't grow.

## Dataset item shape

Each Opik Test Suite item has three top-level keys:

```json
{
  "description": "simple-duplicate-cite-issue",
  "data": {
    "scenario_id": "simple-duplicate-cite-issue",
    "scenario": "default",
    "spec": {
      "owner": "trainer-org",
      "name": "trainer-lib",
      "readme": "# trainer-lib\n\n...",
      "issues": [
        {"number": 999, "title": "Training hangs on 4 GPUs", "body": "..."},
        {"number": 412, "title": "multi-gpu deadlock", "body": "fixed in dist.py",
         "state": "closed", "labels": ["bug", "distributed"]}
      ],
      "files": {
        "src/distributed/all_reduce.py": "...",
        "src/trainer.py": "..."
      }
    },
    "target_issue": 999,
    "expected": {
      "should_escalate": false,
      "should_cite_issue": 412,
      "root_cause_files": ["src/distributed/all_reduce.py"]
    }
  },
  "assertions": [
    "The response references issue #412 as a related or duplicate issue.",
    "The response identifies src/distributed/all_reduce.py as where the bug lives.",
    "final_labels does not contain 'Escalated-request'.",
    "search_queries contains at least one query mentioning 'gpu', 'multi', 'hang', or 'deadlock'."
  ]
}
```

- `scenario` names a registered builder (`"default"` covers most cases).
- `spec` is the JSON the builder consumes — issues, files, readme, optional owner/name.
- `target_issue` is the issue Scout triages this run; the others are search-result fodder.
- `expected` is for the human writing assertions and for any code-level judging — it's surfaced in the task input so judges can compare against it.
- `assertions` is the Opik-Test-Suite-native LLM-judged criteria.

See `evals/starter_scenarios.py` for five worked examples covering duplicate citation, code investigation, escalation, spam, and search-degraded resilience.

## How the eval driver works

`evals/run_eval.py` is the bridge between Opik's `run_tests` and the agent loop:

```python
def task(item: dict) -> dict:
    data = item.get("data", item)
    sim = build(data["scenario"], data["spec"])
    target = int(data["target_issue"])

    comment, _ = run_agent(
        sim,
        target,
        client=client,
        system_prompt=SYSTEM_PROMPT,
        escalation_tag=SCOUT_ESCALATION_TAG,
        repo_owner=sim.owner,
        repo_name=sim.name,
        opik_project=EVAL_OPIK_PROJECT,
        ...
    )

    final_issue = sim.issue(target)
    return {
        "input": {"target_issue": target, "issues": data["spec"].get("issues", [])},
        "output": comment,
        # state and side effects — so LLM judges can grade more than the text
        "final_labels": final_issue["labels"],
        "applied_labels": [...],
        "search_queries": [...],
    }
```

A fresh `GitHubSimulator` is built per item from its `spec`. The agent runs end-to-end against it. The task returns:

- `output` — the comment text Scout produced
- `final_labels` — the issue's labels after the run (state, not call log)
- `applied_labels` — labels Scout actually called `apply_label` with
- `search_queries` — what queries Scout issued to `search_issues`

Assertions can reference any of these. Examples:

- *"Output cites issue #412 as a duplicate"* — judges read `output`.
- *"`final_labels` contains 'Escalated-request'"* — judges read `final_labels`.
- *"`search_queries` includes at least one query that mentions 'gpu' or 'deadlock'"* — probes query formulation, which is a real Scout failure mode.

## Opik tracing

Both providers route traces through Opik identically — tracing lives at the agent/tool/LLM layer, not the provider layer. What differs is the project name:

- Production runs (`scout.triage` → `GitHubProvider`) trace to `scout:<owner>/<repo>`.
- Eval runs (`evals/run_eval.py` → `GitHubSimulator`) trace to `scout-eval` (override with `SCOUT_EVAL_OPIK_PROJECT`).

Different projects keep prod triage and eval experiments visually separate in the Opik UI. Eval runs are noisy — you may run a 5-item suite many times while iterating on the prompt — and you don't want that drowning out real triage traces.

Each item's run becomes one trace under one Experiment created by `opik.run_tests`. The trace tree contains the top-level `scout-issue-{N}` span, every Anthropic call (via `track_anthropic`), and every tool invocation (via `@opik.track(type="tool")`).

## Running the eval

**Seed the Test Suite once (or after editing scenarios):**

```bash
OPIK_API_KEY=... OPIK_WORKSPACE=... \
  python -m evals.seed_test_suite
```

This is idempotent on the suite name (default: `scout-triage-regression`). It creates the suite if it doesn't exist, then inserts items. Re-running inserts again — to start clean, delete the suite in the Opik UI first.

**Run an experiment:**

```bash
ANTHROPIC_API_KEY=... OPIK_API_KEY=... OPIK_WORKSPACE=... \
  GITHUB_TOKEN=unused \
  SCOUT_GITHUB_REPO_OWNER=x SCOUT_GITHUB_REPO_NAME=y \
  SCOUT_EXPERIMENT_NAME=baseline-v1 \
  python -m evals.run_eval
```

`GITHUB_TOKEN` must be set because the triage module validates it at import time. For all-simulated suites the value is unused — `unused` is fine. **For suites that include real-GitHub-mode scenarios** (specs with no `files` key), it must be a real token with read access to the target repo. `SCOUT_EXPERIMENT_NAME` is treated as a *prefix*: each run gets `{prefix}-YYYY-MM-DD-HH-MM-SS` appended, so re-running without changing the env var produces a fresh, chronologically sortable experiment in the Opik UI.

## Adding a scenario

1. **Open `evals/starter_scenarios.py`** and append a new item to `STARTER_SCENARIOS` following the shape above.
2. **Write 3–6 specific assertions** that a judge can answer yes/no clearly. Reference the surfaced output keys (`output`, `final_labels`, `applied_labels`, `search_queries`) when behavior matters more than text.
3. **If your scenario needs programmable behavior** (flaky tools, multi-call state changes), register a new builder with `@register("your-name")` in `src/scout/providers/scenarios.py` and reference it via `"scenario": "your-name"`. The base `_default` builder is composable — call it inside your builder and then mutate the result.
4. **Run `pytest tests/test_triage.py -k Starter`** — three parametrized validation tests will check your scenario is structurally valid before you push.
5. **Re-seed and re-run:**

   ```bash
   python -m evals.seed_test_suite
   python -m evals.run_eval
   ```

## Files

| Path | Purpose |
|---|---|
| `src/scout/providers/base.py` | `RepositoryProvider` protocol |
| `src/scout/providers/github.py` | Production backend (PyGithub) |
| `src/scout/providers/simulator.py` | In-memory simulator with fluent builders |
| `src/scout/providers/scenarios.py` | Scenario builder registry + default/search-rate-limited builders |
| `src/scout/agent.py` | Agent loop, tool definitions, `make_tools`, `make_client` |
| `src/scout/triage.py` | Production entry point (env parsing, prompt loading, `main()`) |
| `evals/run_eval.py` | Opik Test Suite driver |
| `evals/starter_scenarios.py` | Five worked starter scenarios |
| `evals/seed_test_suite.py` | Idempotent suite seeder |
