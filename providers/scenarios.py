"""Scenario builder registry: bridges JSON dataset items to a GitHubSimulator.

Opik dataset rows are JSON; simulator behavior is Python. The registry lets a
JSON `spec` reference a named builder, so most scenarios stay declarative while
the long tail can register custom Python builders for programmable behavior
(flaky search, dynamic state, etc.) without growing the dataset schema.
"""
from __future__ import annotations

from typing import Callable

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

    Expected shape:
        {
          "owner": "sim",                 # optional
          "name": "repo",                 # optional
          "readme": "...",                # optional
          "files": {"src/foo.py": "..."}, # path -> content
          "issues": [
            {"number": 999, "title": "...", "body": "...",
             "state": "open", "author": "u1", "labels": [], "comments": []},
            ...
          ]
        }
    """
    sim = GitHubSimulator(spec.get("owner", "sim"), spec.get("name", "repo"))
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
