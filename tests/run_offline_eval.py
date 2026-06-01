#!/usr/bin/env python3
"""Run Scout offline against an Opik dataset of real captured issues.

Each dataset item is a row from the CSV produced by fetch_test_issues.py:
    number, title, state, author, labels, body, comments_json

The eval uses GitHubProvider for real repo file access (list_directory,
get_file_contents, fetch_readme, search_issues) but serves the issue data
from the stored snapshot — so Scout sees the exact issue that was captured
rather than whatever the live issue looks like today.

Env vars (on top of the normal Scout config in .env):
    SCOUT_OFFLINE_DATASET_NAME   — Opik dataset name (default: "scout-test-issues")
    SCOUT_OFFLINE_OPIK_PROJECT   — Opik project for traces  (default: "scout-offline-eval")
    SCOUT_EXPERIMENT_NAME        — prefix for the experiment name (default: "scout-offline-eval")
"""
from __future__ import annotations

import logging
import os
import sys
# scout.py calls _get_issue_number() at module level; give it a dummy value so
# the import succeeds — the eval never uses ISSUE_NUMBER from scout directly.
os.environ.setdefault("ISSUE_NUMBER", "1")

# Resolve the repo root so relative imports work when run from the tests/ dir.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

import opik
from opik.evaluation.metrics import Usefulness
from dotenv import load_dotenv

load_dotenv()

from agent import make_client, run_agent
from providers.github import GitHubProvider
from scout import (
    ANTHROPIC_API_KEY,
    GITHUB_TOKEN,
    MAX_TOKENS,
    MODEL,
    SCOUT_ESCALATION_TAG,
    SYSTEM_PROMPT,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATASET_NAME = os.environ.get("SCOUT_OFFLINE_DATASET_NAME", "scout-test-issues")
EVAL_OPIK_PROJECT = os.environ.get("SCOUT_OFFLINE_OPIK_PROJECT", "scout:comet-ml/scout-test-repo")
EXPERIMENT_NAME_PREFIX = os.environ.get("SCOUT_EXPERIMENT_NAME", "scout-offline-eval")
REPO_OWNER = os.environ.get("SCOUT_GITHUB_REPO_OWNER", "").strip()
REPO_NAME = os.environ.get("SCOUT_GITHUB_REPO_NAME", "").strip()


def _experiment_name() -> str:
    return f"{EXPERIMENT_NAME_PREFIX}-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"


class _SnapshotProvider(GitHubProvider):
    """GitHubProvider that serves a pre-captured issue snapshot.

    All file-side operations (list_directory, get_file_contents, fetch_readme,
    search_issues) go to the real GitHub repo. Only get_issue_data is overridden
    to return the stored snapshot so the eval is reproducible.
    """

    def __init__(self, token: str, owner: str, name: str, snapshot: dict):
        super().__init__(token, owner, name)
        self._snapshot = snapshot

    def get_issue_data(self, issue_number: int) -> dict:  # noqa: ARG002
        return self._snapshot


def _parse_item(item: dict) -> dict:
    """Parse a flat CSV-uploaded dataset item into the issue-data dict."""
    import json as _json
    labels_raw = item.get("labels", "")
    labels = [l.strip() for l in labels_raw.split(",") if l.strip()] if labels_raw else []
    try:
        comments = _json.loads(item.get("comments_json") or "[]")
    except _json.JSONDecodeError:
        comments = []
    return {
        "number": int(item["number"]),
        "title": item.get("title", ""),
        "body": item.get("body", ""),
        "state": item.get("state", "open"),
        "author": item.get("author", "unknown"),
        "labels": labels,
        "comments": comments,
    }


def eval_task(item: dict) -> dict:
    snapshot = _parse_item(item)
    issue_number = snapshot["number"]
    logger.info("Evaluating issue #%d: %s", issue_number, snapshot["title"][:60])

    client = make_client(ANTHROPIC_API_KEY, opik_project=EVAL_OPIK_PROJECT)
    provider = _SnapshotProvider(GITHUB_TOKEN, REPO_OWNER, REPO_NAME, snapshot)

    comment, _trace_id = run_agent(
        provider,
        issue_number,
        client=client,
        system_prompt=SYSTEM_PROMPT,
        escalation_tag=SCOUT_ESCALATION_TAG,
        repo_owner=REPO_OWNER,
        repo_name=REPO_NAME,
        opik_project=EVAL_OPIK_PROJECT,
        model=MODEL,
        max_tokens=MAX_TOKENS,
    )

    return {
        "input": {
            "issue_number": issue_number,
            "title": snapshot["title"],
            "body": snapshot["body"],
        },
        "output": comment,
    }


def main() -> None:
    if not REPO_OWNER or not REPO_NAME:
        sys.exit("Set SCOUT_GITHUB_REPO_OWNER and SCOUT_GITHUB_REPO_NAME in your .env")

    opik_client = opik.Opik()
    dataset = opik_client.get_dataset(DATASET_NAME)

    experiment_name = _experiment_name()
    logger.info("Dataset: %s  |  Experiment: %s", DATASET_NAME, experiment_name)

    opik.evaluate(
        dataset=dataset,
        task=eval_task,
        experiment_name=experiment_name,
        project_name=EVAL_OPIK_PROJECT,
        scoring_metrics=[ Usefulness(model="claude-sonnet-4-6")] #TODO: add more metrics and make model selectable via env var
    )

    logger.info("Done — view results in the Opik UI under project '%s'", EVAL_OPIK_PROJECT)


if __name__ == "__main__":
    main()