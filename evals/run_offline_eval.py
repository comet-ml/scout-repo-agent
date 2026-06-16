#!/usr/bin/env python3
"""Run Scout offline against an Opik dataset of triage scenarios.

Each dataset item must use the simulator format:
    {
      "scenario": "default",          # builder name from providers/scenarios.py
      "spec": {
        "owner": "...", "name": "...",
        "readme": "...",               # optional
        "files": {"src/foo.py": "..."}, # omit for real-GitHub file access
        "issues": [...]
      },
      "target_issue": 42
    }

Env vars (on top of the normal Scout config in .env):
    SCOUT_GITHUB_DATASET_NAME  — Opik dataset to evaluate (default: "scout-triage-inputs")
    SCOUT_EVAL_OPIK_PROJECT    — Opik project for traces (default: "scout-eval")
    SCOUT_EXPERIMENT_NAME      — prefix for the experiment name (default: "scout-offline-eval")
"""
from __future__ import annotations

import logging
import os
import sys
# scout.triage calls _get_issue_number() at module level; give it a dummy value so
# the import succeeds — the eval never uses ISSUE_NUMBER from triage directly.
os.environ.setdefault("ISSUE_NUMBER", "1")

# Resolve the repo root so relative imports work when run from the evals/ dir.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

import opik
from opik.evaluation.metrics import Usefulness
from dotenv import load_dotenv

load_dotenv(override=True)

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

DATASET_NAME = os.environ.get("SCOUT_GITHUB_DATASET_NAME", "scout-triage-inputs")
EVAL_OPIK_PROJECT = os.environ.get("SCOUT_EVAL_OPIK_PROJECT", "scout-eval")
EXPERIMENT_NAME_PREFIX = os.environ.get("SCOUT_EXPERIMENT_NAME", "scout-offline-eval")


def _experiment_name() -> str:
    return f"{EXPERIMENT_NAME_PREFIX}-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"


def make_eval_task(system_prompt: str):
    client = make_client(ANTHROPIC_API_KEY, opik_project=EVAL_OPIK_PROJECT)

    def eval_task(item: dict) -> dict:
        data = item.get("data", item)
        if "spec" not in data or "target_issue" not in data:
            keys = list(data.keys())
            raise ValueError(
                f"Dataset item is missing 'spec' or 'target_issue'. Got keys: {keys}. "
                "Re-seed the dataset using evals/utils/seed_offline_dataset.py — "
                "old CSV-format rows must be removed first (delete the dataset in the Opik UI)."
            )
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
        applied = [c[2] for c in sim.calls if len(c) == 3 and c[0] == "apply_label"]
        searches = [c[1] for c in sim.calls if len(c) == 2 and c[0] == "search_issues"]

        return {
            "input": {"target_issue": target, "issues": spec.get("issues", [])},
            "output": comment,
            "final_labels": final_issue["labels"],
            "applied_labels": applied,
            "search_queries": searches,
        }

    return eval_task


def main() -> None:
    opik_client = opik.Opik()
    dataset = opik_client.get_dataset(DATASET_NAME)
    system_prompt = load_system_prompt()
    prompt_obj = opik_client.get_chat_prompt(name=SCOUT_OPIK_PROMPT_NAME)

    experiment_name = _experiment_name()
    logger.info("Dataset: %s  |  Experiment: %s", DATASET_NAME, experiment_name)

    opik.evaluate(
        dataset=dataset,
        task=make_eval_task(system_prompt),
        experiment_name=experiment_name,
        project_name=EVAL_OPIK_PROJECT,
        experiment_config={"model": MODEL, "max_tokens": MAX_TOKENS, "prompt_name": SCOUT_OPIK_PROMPT_NAME},
        prompts=[prompt_obj] if prompt_obj else [],
        scoring_metrics=[Usefulness(model="claude-sonnet-4-6")],
    )

    logger.info("Done — view results in the Opik UI under project '%s'", EVAL_OPIK_PROJECT)


if __name__ == "__main__":
    main()