"""Metric functions for Scout prompt optimisation.

All metrics share the same invoke_agent output contract:
    llm_output = JSON string {"comment": str, "escalated": bool}

Phases:
    Phase 1 — flag_only_metric      (escalation flag only, no LLM judge)
    Phase 2 — triage_accuracy       (flag 0.6 + reply quality judge 0.4)
"""
from __future__ import annotations

import json
import os
import sys

_repo_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _repo_root)
sys.path.insert(0, os.path.join(_repo_root, "src"))

import litellm  # noqa: E402
from opik.evaluation.metrics import AnswerRelevance  # noqa: E402
from opik.evaluation.metrics.score_result import ScoreResult  # noqa: E402

from scout.triage import OPIK_PROJECT, SCOUT_ESCALATION_TAG  # noqa: E402

JUDGE_MODEL = "anthropic/claude-haiku-4-5-20251001"

JUDGE_PROMPT = """\
You are evaluating a GitHub issue reply written by Scout, an AI triage agent.

Scout's role is to:
- Help the issue author understand whether their issue is a bug or not
- Clarify the code behaviour where possible
- NOT suggest fixes or code changes
- Ask for reproduction steps if a bug is suspected
- Link to relevant documentation when helpful
- Always introduce itself as Scout with a friendly tone
- Escalate only when the issue requires a major design decision, breaking API change,
  or architectural discussion needing maintainer consensus

---

GitHub Issue:
{issue}

Scout's Reply:
{reply}

Escalation Applied: {escalated}

---

Score the reply from 0.0 to 1.0:
- 0.0: fails a hard rule (no Scout intro, suggests fixes, wrong tone, escalation contradicts reply)
- 0.5: meets hard rules but vague — missing repro steps or docs when clearly needed
- 0.75: solid reply with minor gaps
- 1.0: excellent — clear, friendly, on-scope, correctly escalated, repro steps/docs where appropriate

Return JSON only: {{"score": float, "reason": "one sentence"}}
"""

_answer_relevance_metric = AnswerRelevance(
    model=JUDGE_MODEL,
    project_name=OPIK_PROJECT,
    require_context=False,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_output(llm_output: str) -> tuple[str, bool]:
    """Parse invoke_agent JSON output into (comment, escalated).

    Falls back to plain string + tag-in-text detection if JSON is malformed.
    """
    try:
        parsed = json.loads(llm_output)
        return parsed["comment"], bool(parsed["escalated"])
    except (json.JSONDecodeError, KeyError):
        return llm_output, SCOUT_ESCALATION_TAG.lower() in llm_output.lower()


def _expected_escalation(dataset_item: dict) -> bool | None:
    """Return the expected escalation bool, or None if not present in the item."""
    data = dataset_item.get("data", dataset_item)
    expected = data.get("expected", {})
    val = expected.get("should_escalate")
    return bool(val) if val is not None else None


# ---------------------------------------------------------------------------
# Phase 1 — flag accuracy only
# ---------------------------------------------------------------------------

def flag_only_metric(dataset_item: dict, llm_output: str) -> ScoreResult:
    """Escalation flag correctness only. No LLM judge call."""
    should_escalate = _expected_escalation(dataset_item)
    if should_escalate is None:
        return ScoreResult(name="flag_accuracy", value=1.0, reason="No expected flag — skipped.")

    _, output_escalated = _parse_output(llm_output)
    correct = output_escalated == should_escalate
    return ScoreResult(
        name="flag_accuracy",
        value=1.0 if correct else 0.0,
        reason="Flag correct." if correct else f"Flag wrong — expected escalate={should_escalate}.",
    )


# ---------------------------------------------------------------------------
# Phase 2 — triage accuracy (flag + reply quality)
# ---------------------------------------------------------------------------

def _reply_quality(issue: str, reply: str, escalated: bool) -> ScoreResult:
    """LLM-as-judge for Scout's reply. Uses JUDGE_MODEL (Haiku) to keep costs low."""
    prompt = JUDGE_PROMPT.format(issue=issue, reply=reply, escalated=escalated)
    response = litellm.completion(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content or ""
    content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(content)
    return ScoreResult(
        name="reply_quality",
        value=float(result["score"]),
        reason=result["reason"],
    )


def triage_accuracy(dataset_item: dict, llm_output: str) -> ScoreResult:
    """Phase 2 — flag accuracy (0.6) + reply quality judge (0.4)."""
    comment, output_escalated = _parse_output(llm_output)
    should_escalate = _expected_escalation(dataset_item)

    flag_score = 1.0
    flag_reason = "No expected flag."
    if should_escalate is not None:
        flag_score = 1.0 if output_escalated == should_escalate else 0.0
        flag_reason = "Flag correct." if flag_score == 1.0 else f"Flag wrong — expected escalate={should_escalate}."

    issue = dataset_item.get("issue_message", "")
    reply_result = _reply_quality(issue, comment, output_escalated)

    combined = (flag_score * 0.6) + (reply_result.value * 0.4)
    return ScoreResult(
        name="triage_accuracy",
        value=combined,
        reason=f"{flag_reason} Reply: {reply_result.reason}",
    )


# ---------------------------------------------------------------------------
# Legacy metrics (kept for reference and future phases)
# ---------------------------------------------------------------------------

def escalation_accuracy(dataset_item: dict, llm_output: str) -> float:
    """Score 1.0 if escalation decision matches expected, 0.0 otherwise."""
    should_escalate = _expected_escalation(dataset_item)
    if should_escalate is None:
        return 1.0
    _, output_escalated = _parse_output(llm_output)
    return 1.0 if output_escalated == should_escalate else 0.0


def answer_relevance(dataset_item: dict, llm_output: str) -> float:
    """AnswerRelevance score for the reply comment."""
    comment, _ = _parse_output(llm_output)
    result = _answer_relevance_metric.score(
        input=dataset_item["issue_message"],
        output=comment,
    )
    return result.value


def scout_quality(dataset_item: dict, llm_output: str) -> float:
    """Combined metric: structural completeness (50%) + escalation accuracy (50%)."""
    comment, output_escalated = _parse_output(llm_output)

    required_sections = ["## Solution", "## Code Investigation", "## Next Steps"]
    structure_score = sum(s in comment for s in required_sections) / len(required_sections)

    should_escalate = _expected_escalation(dataset_item)
    escalation_score = 1.0 if should_escalate is None else (
        1.0 if output_escalated == should_escalate else 0.0
    )

    return 0.5 * structure_score + 0.5 * escalation_score
