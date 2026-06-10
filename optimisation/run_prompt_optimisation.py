#!/usr/bin/env python3
"""Optimise the Scout system prompt against the scout-issues-with-github-sim dataset.

Uses the Opik chat prompt named by SCOUT_OPIK_PROMPT_NAME as the starting point,
runs each dataset item through the full Scout agent loop with the simulated GitHub
environment, and scores on escalation accuracy. The best prompt is saved back to
Opik as a new version of the same chat prompt.

Env vars (on top of the normal Scout config in .env):
    SCOUT_OPIK_PROMPT_NAME        — chat prompt to optimise (default: scout-triage-system-prompt)
    SCOUT_OFFLINE_DATASET_NAME    — Opik dataset name (default: scout-issues-with-github-sim)
"""
from __future__ import annotations

import json
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

import opik  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from opik_optimizer import ChatPrompt, MetaPromptOptimizer  # noqa: E402
from opik_optimizer.agents.optimizable_agent import OptimizableAgent  # noqa: E402
from opik_optimizer.utils.prompt_library import PromptLibrary  # noqa: E402

load_dotenv()

from scout.agent import make_client, run_agent  # noqa: E402
from scout.providers.scenarios import build  # noqa: E402
from scout.triage import (  # noqa: E402
    ANTHROPIC_API_KEY,
    MAX_TOKENS,
    MODEL,
    OPIK_PROJECT,
    SCOUT_ESCALATION_TAG,
)
from metrics import triage_accuracy  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATASET_NAME = os.environ.get("SCOUT_OFFLINE_DATASET_NAME", "scout-triage-optimisation-runs-v2")
PROMPT_NAME = os.environ.get("SCOUT_OPIK_PROMPT_NAME", "scout-triage-system-prompt-initial")


class ScoutAgent(OptimizableAgent):
    """Runs the full Scout agent loop for one dataset item using the simulated GitHub environment."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = make_client(ANTHROPIC_API_KEY, opik_project=OPIK_PROJECT)

    def invoke_agent(
        self,
        prompts: dict[str, ChatPrompt],
        dataset_item: dict[str, Any],
        allow_tool_use: bool = False,
        seed: int | None = None,
    ) -> str:
        prompt = next(iter(prompts.values()))

        messages = prompt.get_messages(dataset_item)
        system_prompt = next(
            (m["content"] for m in messages if m.get("role") == "system"), ""
        )
        if not system_prompt:
            logger.warning("invoke_agent: no system prompt in candidate — skipping")
            return ""

        data = dataset_item.get("data", dataset_item)
        scenario = data.get("scenario", "default")
        spec = data["spec"]
        target = int(data["target_issue"])

        sim = build(scenario, spec)
        comment, _ = run_agent(
            sim,
            target,
            client=self._client,
            system_prompt=system_prompt,
            escalation_tag=SCOUT_ESCALATION_TAG,
            repo_owner=sim.owner,
            repo_name=sim.name,
            opik_project=OPIK_PROJECT,
            model=MODEL,
            max_tokens=MAX_TOKENS,
        )
        escalated = SCOUT_ESCALATION_TAG in sim.get_issue_data(target).get("labels", [])
        return json.dumps({"comment": comment or "", "escalated": escalated})


def _scout_reasoning_override(prompts: PromptLibrary) -> None:
    """Inject Scout-specific task context into the meta-LLM's reasoning prompt.

    Replaces enable_context dataset sampling so the optimizer understands the
    task domain without advertising dataset fields as template variables.
    """
    original = prompts.get("reasoning_system")
    prompts.set(
        "reasoning_system",
        original + """

Task context: You are optimising the system prompt for Scout, a GitHub issue triage agent.
Scout receives a GitHub issue (title, body, author, labels) and must:
1. Decide whether to escalate (true) or not (false)
2. Write a reply to the issue author

Escalation means: the issue requires a major design decision, breaking API change, or
architectural discussion that needs maintainer consensus.
No escalation means: bugs, feature requests, duplicate reports, spam — things Scout
can investigate and respond to directly.

The metric scoring Scout evaluates both escalation accuracy (60%) and reply quality (40%).
A high-quality reply: introduces Scout by name, uses a friendly tone, does NOT suggest
code fixes, asks for repro steps when a bug is suspected, and is consistent with the
escalation decision.

Scout has access to tools that explore the repository codebase and search existing issues.
It does NOT use template variables in its prompt — do not add placeholders like {data} or
{issue_message} to the prompt you generate.
""",
    )


def main() -> None:
    opik_client = opik.Opik()
    dataset = opik_client.get_dataset(DATASET_NAME)

    chat_prompt_obj = opik_client.get_chat_prompt(
        name=PROMPT_NAME,
        project_name=OPIK_PROJECT,
    )
    if chat_prompt_obj is None:
        sys.exit(f"ERROR: Opik chat prompt {PROMPT_NAME!r} not found in project {OPIK_PROJECT!r}.")

    initial_prompt = ChatPrompt(messages=chat_prompt_obj.template)

    optimizer = MetaPromptOptimizer(
        model=f"anthropic/{MODEL}",
        model_parameters={"temperature": 0.0},
        prompts_per_round=4,
        n_threads=4,
        enable_context=False,
        prompt_overrides=_scout_reasoning_override,
        seed=42,
        skip_perfect_score=False,
    )

    logger.info("Starting optimisation: prompt=%r  dataset=%r", PROMPT_NAME, DATASET_NAME)

    result = optimizer.optimize_prompt(
        prompt=initial_prompt,
        dataset=dataset,
        metric=triage_accuracy,
        agent=ScoutAgent(project_name=OPIK_PROJECT),
        n_samples=10,
        project_name=OPIK_PROJECT,
        max_trials=2,
    )

    result.display()

    logger.info(
        "Review results in the Opik dashboard and manually promote the best prompt "
        "to a new version of %r if you decide it is better.",
        PROMPT_NAME,
    )


if __name__ == "__main__":
    main()
