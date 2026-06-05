#!/usr/bin/env python3
"""Optimise the Scout system prompt against the scout-issues-with-github-sim dataset.

Uses the Opik chat prompt named by SCOUT_OPIK_PROMPT_NAME as the starting point,
runs each dataset item through the full Scout agent loop with the simulated GitHub
environment, and scores on escalation accuracy. The best prompt is saved back to
Opik as a new version of the same chat prompt.

Env vars (on top of the normal Scout config in .env):
    SCOUT_OPIK_PROMPT_NAME        — chat prompt to optimise (default: scout-triage-system-prompt)
    SCOUT_OFFLINE_DATASET_NAME    — Opik dataset name (default: scout-issues-with-github-sim)
    SCOUT_OFFLINE_OPIK_PROJECT    — Opik project for traces (default: scout-prompt-optimisation)
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

# Stub env vars required by scout.triage at import time.
os.environ.setdefault("ISSUE_NUMBER", "1")
os.environ.setdefault("GITHUB_TOKEN", "unused")
os.environ.setdefault("SCOUT_GITHUB_REPO_OWNER", "comet-ml")
os.environ.setdefault("SCOUT_GITHUB_REPO_NAME", "scout-test-repo")

_repo_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _repo_root)
sys.path.insert(0, os.path.join(_repo_root, "src"))

import opik
from dotenv import load_dotenv
from opik_optimizer import ChatPrompt, MetaPromptOptimizer
from opik_optimizer.agents.optimizable_agent import OptimizableAgent

load_dotenv()

from scout.agent import make_client, run_agent
from scout.providers.scenarios import build
from scout.triage import (
    ANTHROPIC_API_KEY,
    MAX_TOKENS,
    MODEL,
    OPIK_PROJECT,
    SCOUT_ESCALATION_TAG,
    _system_messages,
    _text_from_chat_prompt,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATASET_NAME = os.environ.get("SCOUT_OFFLINE_DATASET_NAME", "scout-issues-with-github-sim")
EVAL_OPIK_PROJECT = os.environ.get("SCOUT_OFFLINE_OPIK_PROJECT", "scout-prompt-optimisation")
PROMPT_NAME = os.environ.get("SCOUT_OPIK_PROMPT_NAME", "scout-triage-system-prompt")


class ScoutAgent(OptimizableAgent):
    """Runs the full Scout agent loop for one dataset item using the simulated GitHub environment."""

    def invoke_agent(
        self,
        prompts: dict[str, ChatPrompt],
        dataset_item: dict[str, Any],
        allow_tool_use: bool = False,
        seed: int | None = None,
    ) -> str:
        system_prompt = list(prompts.values())[0].system or ""
        data = dataset_item.get("data", dataset_item)
        scenario = data.get("scenario", "default")
        spec = data["spec"]
        target = int(data["target_issue"])

        sim = build(scenario, spec)
        client = make_client(ANTHROPIC_API_KEY, opik_project=EVAL_OPIK_PROJECT)

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
        return comment


def escalation_accuracy(dataset_item: dict, llm_output: str) -> float:
    """Score 1.0 if escalation decision matches expected, 0.0 otherwise.

    Items without an expected.should_escalate field score 1.0 so they don't
    dilute the signal.
    """
    data = dataset_item.get("data", dataset_item)
    expected = data.get("expected", {})

    if "should_escalate" not in expected:
        return 1.0

    should_escalate: bool = expected["should_escalate"]
    output_escalated = SCOUT_ESCALATION_TAG.lower() in llm_output.lower()

    return 1.0 if output_escalated == should_escalate else 0.0


def main() -> None:
    opik_client = opik.Opik()
    dataset = opik_client.get_dataset(DATASET_NAME)

    chat_prompt_obj = opik_client.get_chat_prompt(
        name=PROMPT_NAME,
        project_name=OPIK_PROJECT,
    )
    if chat_prompt_obj is None:
        sys.exit(f"ERROR: Opik chat prompt {PROMPT_NAME!r} not found in project {OPIK_PROJECT!r}.")

    system_message = _text_from_chat_prompt(chat_prompt_obj)
    initial_prompt = ChatPrompt(system=system_message, user="{input}")

    optimizer = MetaPromptOptimizer(
        model=f"anthropic/{MODEL}",
        model_parameters={"temperature": 0.0},
        prompts_per_round=4,
        n_threads=4,
        enable_context=True,
        seed=42,
    )

    logger.info("Starting optimisation: prompt=%r  dataset=%r", PROMPT_NAME, DATASET_NAME)

    result = optimizer.optimize_prompt(
        prompt=initial_prompt,
        dataset=dataset,
        metric=escalation_accuracy,
        agent=ScoutAgent(project_name=EVAL_OPIK_PROJECT),
        n_samples=10,
    )

    result.display()

    # Save the best system prompt back to Opik as a new version of the chat prompt.
    best_prompt = result.prompt if isinstance(result.prompt, ChatPrompt) else list(result.prompt.values())[0]
    best_system = best_prompt.system or ""
    opik_client.create_chat_prompt(
        name=PROMPT_NAME,
        messages=_system_messages(best_system),
        project_name=OPIK_PROJECT,
    )
    logger.info("Best prompt saved to Opik under %r as a new version.", PROMPT_NAME)


if __name__ == "__main__":
    main()
