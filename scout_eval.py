#!/usr/bin/env python3
"""Run Scout against an Opik Test Suite of triage scenarios.

Each dataset item carries:
    - "scenario": a registered builder name (default: "default")
    - "spec": JSON consumed by the builder to populate a GitHubSimulator
    - "target_issue": the issue number Scout should triage

The task function builds a fresh simulator per item, runs the agent, and
returns the comment text plus side-effect info so Opik's LLM-judged
assertions can grade both the output and what Scout did.

Env vars (in addition to scout.py's normal config):
    SCOUT_TEST_SUITE_NAME      — Opik Test Suite name (required)
    SCOUT_EXPERIMENT_NAME      — Experiment-name prefix; a YYYY-MM-DD-HH-MM-SS
                                 timestamp is appended so each run is unique
                                 (default: "scout-eval")
    SCOUT_EVAL_OPIK_PROJECT    — Opik project for eval traces
                                 (default: "scout-eval", keeps prod project clean)

Note: scout.py validates GITHUB_TOKEN at import time. The simulator doesn't
use it, so any non-empty value (e.g. "unused") is fine for eval runs.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import opik

from agent import make_client, run_agent
from providers.scenarios import build
from scout import (
    ANTHROPIC_API_KEY,
    MAX_TOKENS,
    MODEL,
    SCOUT_ESCALATION_TAG,
    load_system_prompt,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


EVAL_OPIK_PROJECT = os.environ.get("SCOUT_EVAL_OPIK_PROJECT", "scout-eval")
TEST_SUITE_NAME = os.environ.get("SCOUT_TEST_SUITE_NAME", "scout-triage-regression")
EXPERIMENT_NAME_PREFIX = os.environ.get("SCOUT_EXPERIMENT_NAME", "scout-eval")


def _experiment_name() -> str:
    """`{prefix}-YYYY-MM-DD-HH-MM-SS` — unique per second, sortable in the UI."""
    return f"{EXPERIMENT_NAME_PREFIX}-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"


def make_task():
    """Build the `task(item)` callable that `opik.run_tests` will invoke per item."""
    client = make_client(ANTHROPIC_API_KEY, opik_project=EVAL_OPIK_PROJECT)
    system_prompt = load_system_prompt()

    def task(item: dict) -> dict:
        data = item.get("data", item)
        scenario = data.get("scenario", "default")
        spec = data["spec"]
        target = int(data["target_issue"])

        sim = build(scenario, spec)
        logger.info("scenario=%s target=#%d", scenario, target)

        comment, _trace_id = run_agent(
            sim,
            target,
            client=client,
            system_prompt=system_prompt,
            escalation_tag=SCOUT_ESCALATION_TAG,
            repo_owner=sim.owner,
            repo_name=sim.name,
            opik_project=EVAL_OPIK_PROJECT,
            model=MODEL,
            max_tokens=MAX_TOKENS,
        )

        final_issue = sim.issue(target)
        applied = [lbl for op, _, lbl in (c for c in sim.calls if len(c) == 3 and c[0] == "apply_label")]
        searches = [q for op, q in (c for c in sim.calls if len(c) == 2 and c[0] == "search_issues")]

        return {
            "input": {"target_issue": target, "issues": spec.get("issues", [])},
            "output": comment,
            # State after the run — assertions can judge real outcomes
            "final_labels": final_issue["labels"],
            "applied_labels": applied,
            "search_queries": searches,
        }

    return task


def main() -> None:
    client = opik.Opik()
    suite = client.get_test_suite(TEST_SUITE_NAME)
    experiment_name = _experiment_name()
    logger.info("Experiment: %s", experiment_name)
    result = opik.run_tests(
        test_suite=suite,
        task=make_task(),
        experiment_name=experiment_name,
    )
    pass_rate = getattr(result, "pass_rate", None)
    if pass_rate is not None:
        logger.info("Pass rate: %s", pass_rate)
    else:
        logger.info("Done — see Opik UI for results.")


if __name__ == "__main__":
    main()
