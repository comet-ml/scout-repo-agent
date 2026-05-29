"""Scenario builder registry: bridges JSON dataset items to a GitHubSimulator.

Opik dataset rows are JSON; simulator behavior is Python. The registry lets a
JSON `spec` reference a named builder, so most scenarios stay declarative while
the long tail can register custom Python builders for programmable behavior
(flaky search, dynamic state, etc.) without growing the dataset schema.
"""
from __future__ import annotations

import os
from typing import Callable

from .github import GitHubProvider
from .simulator import GitHubSimulator


SCENARIO_BUILDERS: dict[str, Callable[[dict], GitHubSimulator]] = {}


def register(name: str):
    """Decorator: register a builder function under a scenario name."""
    def deco(fn: Callable[[dict], GitHubSimulator]) -> Callable[[dict], GitHubSimulator]:
        SCENARIO_BUILDERS[name] = fn
        return fn
    return deco


def build(scenario: str, spec: dict) -> GitHubSimulator:
    """Look up a builder by name and apply it to the spec."""
    if scenario not in SCENARIO_BUILDERS:
        raise ValueError(
            f"Unknown scenario {scenario!r}. Registered: {sorted(SCENARIO_BUILDERS)}"
        )
    return SCENARIO_BUILDERS[scenario](spec)


@register("default")
def _default(spec: dict) -> GitHubSimulator:
    """Populate a fresh simulator from a flat JSON spec.

    Two modes, selected by the presence of `files`:

    **Simulated mode** — `spec["files"]` present. README and file contents
    come from the spec; nothing hits the network:
        {
          "owner": "sim", "name": "repo",         # optional
          "readme": "...",                        # optional
          "files": {"src/foo.py": "..."},
          "issues": [...]
        }

    **Real-GitHub mode** — `spec["files"]` absent. README, list_directory,
    and file contents are fetched from real GitHub via `GitHubProvider`.
    Requires `GITHUB_TOKEN` in the environment. `owner`/`name` must name a
    real repo. Issues, search, and writes remain simulated:
        {
          "owner": "comet-ml", "name": "opik",
          "issues": [...]
        }
    """
    owner = spec.get("owner", "sim")
    name = spec.get("name", "repo")
    upstream: GitHubProvider | None = None

    if "files" not in spec:
        if "readme" in spec:
            raise ValueError(
                "Scenario spec omits 'files' (real-GitHub mode) but includes 'readme'. "
                "In real-GitHub mode the README is fetched from GitHub — remove 'readme'."
            )
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token or token == "unused":
            raise ValueError(
                f"Scenario for {owner}/{name} omits 'files' (real-GitHub mode) but "
                "GITHUB_TOKEN is not set to a real token. Either set GITHUB_TOKEN "
                "or add a 'files' key to use simulated mode."
            )
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
    """Same data as default, but search returns [] after the second call.

    Useful for testing whether Scout still produces a reasonable answer when
    its search tool stops returning results mid-investigation.
    """
    sim = _default(spec)
    counter = [0]

    def handler(query: str, max_results: int, issues: dict) -> list[dict]:
        counter[0] += 1
        if counter[0] > 2:
            return []
        return GitHubSimulator._default_search(query, max_results, issues)

    sim.set_search_handler(handler)
    return sim
