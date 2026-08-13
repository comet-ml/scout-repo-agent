#!/usr/bin/env python3
"""Opik Agent Playground endpoint for Scout.

Exposes Scout's triage loop as a playground-runnable agent, backed by the
GitHubSimulator rather than real GitHub. Nothing touches the network except the
Anthropic and Opik calls: issues, repo files, search, labels, and comments are
all simulated, so a playground run has no side effects on any repository.

Everything you would want to vary between runs is an entrypoint parameter, set
from the playground's Test-input form. The only environment variables read are
credentials and connection settings:

    ANTHROPIC_API_KEY, OPIK_API_KEY, OPIK_WORKSPACE

Run it with:

    opik endpoint --project scout-playground -- python scout_endpoint.py

Deliberately does not import scout.triage. That module validates GITHUB_TOKEN,
SCOUT_GITHUB_REPO_OWNER/NAME and the Opik credentials at import time and derives
its project and prompt version from the environment — so importing it would drag
all of that config back in. scout.agent and scout.providers take everything as
arguments, so the prompt is fetched here instead (see _system_prompt).
"""
import os
import sys
import time

import opik
from dotenv import load_dotenv
from opik.config import OpikConfig

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from evals.utils.starter_scenarios import STARTER_SCENARIOS  # noqa: E402
from scout.agent import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    make_client,
    run_agent,
)
from scout.providers.scenarios import build  # noqa: E402

SCENARIOS = {s["data"]["scenario_id"]: s["data"] for s in STARTER_SCENARIOS}
DEFAULT_SCENARIO = "simple-duplicate-cite-issue"

# `opik endpoint --project X` reaches the SDK config as the default project name,
# so this follows whatever --project was passed with nothing to configure here.
# It has to be applied to the root span as well as the nested ones: a child span
# asking for a project its parent does not share is coerced to the parent's
# project, which is what leaves the playground showing a lone root span.
# run_agent also only instruments tools and LLM calls when given a project name.
PROJECT = OpikConfig().project_name


def _system_prompt(name: str, project: str, version: str) -> str:
    """Fetch Scout's system prompt from the Opik prompt library.

    Blank `version` means latest. Resolution is by version rather than by
    environment: no prompt version is linked to an environment yet, and
    OPIK_ENVIRONMENT is left alone here so it keeps tagging traces.
    """
    chat = opik.Opik().get_chat_prompt(
        name=name, project_name=project, **({"version": version} if version else {})
    )
    if chat is None:
        at = f" at version {version!r}" if version else ""
        raise ValueError(f"No chat prompt {name!r} in project {project!r}{at}")
    for message in chat.template:
        if message.get("role") == "system":
            return message["content"]
    return "\n\n".join(m.get("content", "") for m in chat.template)


@opik.track(entrypoint=True, project_name=PROJECT)
def triage_issue(
    scenario_id: str = DEFAULT_SCENARIO,
    prompt_name: str = "scout-system-prompt",
    prompt_project: str = "scout:comet-ml/opik",
    prompt_version: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    escalation_tag: str = "Escalated-request",
) -> str:
    """Triage a simulated GitHub issue and return the comment Scout would post.

    scenario_id — which simulated repo/issue to triage. One of:
      simple-duplicate-cite-issue (bug with a findable duplicate),
      clear-bug-no-duplicate (bug, no duplicate),
      escalation-breaking-change (should escalate),
      spam-off-topic (should decline),
      search-rate-limited-resilience (search degrades mid-run).
    prompt_name / prompt_project — where to read the system prompt from.
    prompt_version — e.g. "v3"; blank means latest.
    model / max_tokens — Anthropic model id and output cap.
    escalation_tag — label Scout applies when escalating (simulated).

    Leave any field blank to use its default.
    """
    scenario_id = (scenario_id or "").strip() or DEFAULT_SCENARIO
    if scenario_id not in SCENARIOS:
        raise ValueError(
            f"Unknown scenario_id {scenario_id!r}. Available: {sorted(SCENARIOS)}"
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    data = SCENARIOS[scenario_id]
    sim = build(data["scenario"], data["spec"])

    comment, _ = run_agent(
        sim,
        int(data["target_issue"]),
        client=make_client(api_key, opik_project=PROJECT),
        system_prompt=_system_prompt(
            (prompt_name or "").strip() or "scout-system-prompt",
            (prompt_project or "").strip() or "scout:comet-ml/opik",
            (prompt_version or "").strip(),
        ),
        escalation_tag=(escalation_tag or "").strip() or "Escalated-request",
        repo_owner=sim.owner,
        repo_name=sim.name,
        opik_project=PROJECT,
        model=(model or "").strip() or DEFAULT_MODEL,
        max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
    )
    # Spans are batched in a background thread and the process stays alive
    # between jobs, so nothing else forces a send before the UI renders.
    opik.flush_tracker()
    return comment


if __name__ == "__main__":
    # Stay alive: `opik endpoint` serves playground jobs from a background
    # thread in this process.
    time.sleep(86400)
