#!/usr/bin/env python3
"""Scout feedback sync: read 👍/👎 reactions on Scout's issue comments and record
them in Opik as human feedback scores.

GitHub emits no event when a reaction is added to a comment, so this runs on a
schedule (see .github/workflows/scout-feedback.yml) and polls recent issues.

The sync is idempotent: Opik upserts feedback scores by (trace_id, name), so each
run simply recomputes the score from the current reaction counts and overwrites
the previous value. No local state is kept.

NOTE: GitHub does not bump an issue's ``updated_at`` when a reaction is added, so
issues whose only recent activity is a reaction may fall outside the scan window
(SCOUT_FEEDBACK_SINCE_DAYS). Widen the window to re-check older issues — because
the upsert is idempotent, re-syncing is always safe.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import opik
from dotenv import load_dotenv
from github import Github

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Matches the marker written by scout._feedback_marker. Keep the two in sync.
MARKER_RE = re.compile(r"<!--\s*scout-feedback\s+trace_id=([0-9a-fA-F-]+)\s*-->")

# Opik feedback-score name. Net helpfulness in [0, 1]: 1.0 = all 👍, 0.0 = all 👎.
FEEDBACK_SCORE_NAME = "user_feedback"

# GitHub reaction content values that count as a vote.
THUMBS_UP = "+1"
THUMBS_DOWN = "-1"


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ValueError(f"Required environment variable {name!r} is not set")
    return val


def _get_repo_owner_name() -> tuple[str, str]:
    """Resolve repo owner/name from config or GITHUB_REPOSITORY env var."""
    owner = os.environ.get("SCOUT_GITHUB_REPO_OWNER", "")
    name = os.environ.get("SCOUT_GITHUB_REPO_NAME", "")
    if not owner or not name:
        github_repo = os.environ.get("GITHUB_REPOSITORY", "")
        if "/" in github_repo:
            owner, name = github_repo.split("/", 1)
    if not owner or not name:
        raise ValueError("Set SCOUT_GITHUB_REPO_OWNER and SCOUT_GITHUB_REPO_NAME")
    return owner, name


def parse_trace_id(comment_body: str) -> str | None:
    """Extract the Opik trace id from a Scout comment's hidden marker, or None."""
    if not comment_body:
        return None
    match = MARKER_RE.search(comment_body)
    return match.group(1) if match else None


def collect_thumbs(reactions) -> tuple[list[str], list[str]]:
    """Collect the GitHub logins that reacted 👍 and 👎, ignoring other reaction
    types. A reaction with no resolvable user is recorded as "unknown"."""
    up_logins, down_logins = [], []
    for reaction in reactions:
        login = getattr(getattr(reaction, "user", None), "login", None) or "unknown"
        if reaction.content == THUMBS_UP:
            up_logins.append(login)
        elif reaction.content == THUMBS_DOWN:
            down_logins.append(login)
    return up_logins, down_logins


def compute_feedback(ups: int, downs: int) -> float | None:
    """Net helpfulness in [0, 1]; None when there are no thumbs votes at all."""
    total = ups + downs
    if total == 0:
        return None
    return ups / total


def format_reason(up_logins: list[str], down_logins: list[str]) -> str:
    """Human-readable attribution for the Opik feedback score, e.g.
    '👍 2 (alice, bob) / 👎 1 (carol) from GitHub'."""
    def part(logins: list[str]) -> str:
        names = ", ".join(logins) if logins else "—"
        return f"{len(logins)} ({names})"
    return f"👍 {part(up_logins)} / 👎 {part(down_logins)} from GitHub"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    github_token = _require("GITHUB_TOKEN")
    opik_api_key = os.environ.get("OPIK_API_KEY", "").strip()
    opik_workspace = os.environ.get("OPIK_WORKSPACE", "").strip()
    owner, name = _get_repo_owner_name()
    since_days = int(os.environ.get("SCOUT_FEEDBACK_SINCE_DAYS", "7"))
    project_name = f"scout:{owner}/{name}"

    if not (opik_api_key and opik_workspace):
        logger.error("OPIK_API_KEY and OPIK_WORKSPACE are required to sync feedback")
        sys.exit(1)

    opik.configure(
        api_key=opik_api_key,
        workspace=opik_workspace,
        force=True,
        automatic_approvals=True,
    )
    opik_client = opik.Opik()

    gh = Github(github_token)
    repo = gh.get_repo(f"{owner}/{name}")
    since = datetime.now(timezone.utc) - timedelta(days=since_days)

    logger.info(
        "Scanning %s/%s for Scout comments on issues updated since %s (%d days)",
        owner, name, since.date().isoformat(), since_days,
    )

    scanned = matched = written = 0
    for issue in repo.get_issues(state="all", sort="updated", direction="desc", since=since):
        scanned += 1
        for comment in issue.get_comments():
            trace_id = parse_trace_id(comment.body or "")
            if not trace_id:
                continue
            matched += 1
            up_logins, down_logins = collect_thumbs(comment.get_reactions())
            ups, downs = len(up_logins), len(down_logins)
            value = compute_feedback(ups, downs)
            if value is None:
                continue
            reason = format_reason(up_logins, down_logins)
            opik_client.log_traces_feedback_scores([{
                "id": trace_id,
                "name": FEEDBACK_SCORE_NAME,
                "value": value,
                "reason": reason,
            }])
            written += 1
            logger.info(
                "issue #%d: trace %s -> %s=%.3f (%s)",
                issue.number, trace_id, FEEDBACK_SCORE_NAME, value, reason,
            )

    opik.flush_tracker()
    logger.info(
        "Done: scanned %d issue(s) updated within %d day(s), matched %d Scout comment(s), "
        "wrote %d feedback score(s). Reactions on issues with no other recent activity may "
        "fall outside this window — widen SCOUT_FEEDBACK_SINCE_DAYS to re-check.",
        scanned, since_days, matched, written,
    )


if __name__ == "__main__":
    main()
