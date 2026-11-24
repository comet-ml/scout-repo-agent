#!/usr/bin/env python3
"""Run Scout against an Opik Test Suite of triage scenarios.

Each test suite item carries:
    - "scenario": a registered builder name (default: "default")
    - "spec": JSON consumed by the builder to populate a GitHubSimulator
    - "target_issue": the issue number Scout should triage

The task function builds a fresh simulator per item, runs the agent, and
returns the comment text plus side-effect info so Opik's LLM-judged
assertions can grade both the output and what Scout did.

Env vars (in addition to the triage module's normal config):
    SCOUT_EVAL_OPIK_PROJECT    — Opik project for eval traces (default: scout-eval)
    SCOUT_EXPERIMENT_NAME      — Experiment-name prefix; a YYYY-MM-DD-HH-MM-SS
                                 timestamp is appended so each run is unique
                                 (default: "scout-eval")
"""
from __future__ import annotations

import logging
import os
import sys

os.environ.setdefault("ISSUE_NUMBER", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

import opik
from dotenv import load_dotenv

os.environ.setdefault("OPIK_ENVIRONMENT", "test")
load_dotenv()

from evals import unpinned_prompt_environment  # noqa: E402
from scout.agent import make_client, run_agent  # noqa: E402
from scout.providers.scenarios import build  # noqa: E402
from scout.triage import (  # noqa: E402
    ANTHROPIC_API_KEY,
    MAX_TOKENS,
    MODEL,
    SCOUT_ESCALATION_TAG,
    SCOUT_OPIK_PROMPT_NAME,
    load_system_prompt,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SUITE_NAME = "scout-triage-regression"
_repo_owner = os.environ.get("SCOUT_GITHUB_REPO_OWNER", "")
_repo_name = os.environ.get("SCOUT_GITHUB_REPO_NAME", "")
_default_project = f"scout:{_repo_owner}/{_repo_name}" if _repo_owner and _repo_name else "scout-eval"
EVAL_OPIK_PROJECT = os.environ.get("SCOUT_EVAL_OPIK_PROJECT") or _default_project
EXPERIMENT_NAME_PREFIX = os.environ.get("SCOUT_EXPERIMENT_NAME", "scout-eval")


def _experiment_name() -> str:
    return f"{EXPERIMENT_NAME_PREFIX}-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"


def make_task():
    client = make_client(ANTHROPIC_API_KEY, opik_project=EVAL_OPIK_PROJECT)
    with unpinned_prompt_environment():
        system_prompt = load_system_prompt()

    def task(item: dict) -> dict:
        data = item.get("data", item)
        scenario = data.get("scenario", "default")
        spec = data["spec"]
        target = int(data["target_issue"])

        sim = build(scenario, spec)
        logger.info("scenario=%s target=#%d", scenario, target)

        comment, _ = run_agent(
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
        applied = [c[2] for c in sim.calls if len(c) == 3 and c[0] == "apply_label"]
        searches = [c[1] for c in sim.calls if len(c) == 2 and c[0] == "search_issues"]

        return {
            "input": {"target_issue": target, "issues": spec.get("issues", [])},
            "output": comment,
            "final_labels": final_issue["labels"],
            "applied_labels": applied,
            "search_queries": searches,
        }

    return task


def main() -> None:
    opik_client = opik.Opik()
    suite = opik_client.get_test_suite(SUITE_NAME)
    prompt_obj = opik_client.get_chat_prompt(name=SCOUT_OPIK_PROMPT_NAME)

    experiment_name = _experiment_name()
    logger.info("Suite: %s  |  Experiment: %s", SUITE_NAME, experiment_name)

    result = opik.run_tests(
        test_suite=suite,
        task=make_task(),
        experiment_name=experiment_name,
        experiment_config={"model": MODEL, "max_tokens": MAX_TOKENS, "prompt_name": SCOUT_OPIK_PROMPT_NAME},
        prompts=[prompt_obj] if prompt_obj else [],
        model="claude-haiku-4-5-20251001",
    )
    pass_rate = getattr(result, "pass_rate", None)
    if pass_rate is not None:
        logger.info("Pass rate: %.2f", pass_rate)
    else:
        logger.info("Done — view results in the Opik UI under project %r.", EVAL_OPIK_PROJECT)


if __name__ == "__main__":
    main()
