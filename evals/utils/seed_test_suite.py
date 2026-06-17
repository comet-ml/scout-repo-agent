#!/usr/bin/env python3
"""Seed the Opik Test Suite with Scout triage scenarios.

Two sources can be combined in one run:

  1. --from-github PATH   Read a JSON file produced by fetch_github_issues.py
                          and insert each issue as a test suite item. Assertions
                          are omitted — add them in the Opik UI after seeding,
                          or pass them programmatically by editing this script.

  2. --from-starter       Insert the synthetic starter scenarios from
                          evals/utils/starter_scenarios.py, which include
                          predefined per-item assertions.

Idempotent on the suite name: if a suite with that name already exists this
script reuses it. Items are inserted regardless — re-running may produce
duplicates. To start clean, delete the suite in the Opik UI before re-seeding.

Env vars:
    OPIK_API_KEY, OPIK_WORKSPACE     — required
    SCOUT_EVAL_OPIK_PROJECT          — project for the suite (default: scout-eval)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import opik
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SUITE_NAME = "scout-triage-regression"
_repo_owner = os.environ.get("SCOUT_GITHUB_REPO_OWNER", "")
_repo_name = os.environ.get("SCOUT_GITHUB_REPO_NAME", "")
_default_project = f"scout:{_repo_owner}/{_repo_name}" if _repo_owner and _repo_name else "scout-eval"
SUITE_PROJECT = os.environ.get("SCOUT_EVAL_OPIK_PROJECT") or _default_project
SUITE_DESCRIPTION = (
    "Regression scenarios for the Scout GitHub issue triage agent. "
    "Each item populates a simulated GitHub via providers/scenarios.py and "
    "runs the agent end-to-end; assertions are LLM-judged."
)


def _issue_to_suite_item(issue: dict, owner: str, name: str) -> dict:
    return {
        "description": f"#{issue['number']} {issue['title'][:60]}",
        "data": {
            "scenario": "default",
            "spec": {
                "owner": owner,
                "name": name,
                # No "files" key → real-GitHub mode for list_directory/get_file_contents
                "issues": [issue],
            },
            "target_issue": issue["number"],
        },
        # assertions omitted — add via the Opik UI or extend this script
    }


def items_from_github(path: str) -> tuple[list[dict], str, str]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    owner = payload["repo"]["owner"]
    name = payload["repo"]["name"]
    items = [_issue_to_suite_item(issue, owner, name) for issue in payload["issues"]]
    logger.info("Loaded %d issues from %s (%s/%s)", len(items), path, owner, name)
    return items, owner, name


def items_from_starter() -> list[dict]:
    from evals.utils.starter_scenarios import GLOBAL_ASSERTIONS, GLOBAL_EXECUTION_POLICY, STARTER_SCENARIOS
    items = []
    for s in STARTER_SCENARIOS:
        item = {"description": s["description"], "data": s["data"]}
        if "assertions" in s:
            item["assertions"] = s["assertions"]
        items.append(item)
    logger.info("Loaded %d starter scenario items", len(items))
    return items, GLOBAL_ASSERTIONS, GLOBAL_EXECUTION_POLICY


def _get_or_create_suite(client: opik.Opik, global_assertions, global_execution_policy):
    try:
    	suite = client.get_test_suite(name=SUITE_NAME, project_name=SUITE_PROJECT)
    	logger.info("Found existing test suite %r — reusing.", SUITE_NAME)
    	return suite
    except Exception as e:
    	logger.info("Test suite %r not found (%s) — creating it.", SUITE_NAME, type(e).__name__)

    return client.create_test_suite(
        name=SUITE_NAME,
        description=SUITE_DESCRIPTION,
        global_assertions=global_assertions,
        global_execution_policy=global_execution_policy,
        project_name=SUITE_PROJECT,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-github", metavar="PATH", help="JSON file from fetch_github_issues.py"
    )
    parser.add_argument(
        "--from-starter",
        action="store_true",
        help="Insert synthetic starter scenarios with predefined assertions",
    )
    args = parser.parse_args()

    if not args.from_github and not args.from_starter:
        parser.error("Specify at least one of --from-github or --from-starter")

    if not os.environ.get("OPIK_API_KEY") or not os.environ.get("OPIK_WORKSPACE"):
        sys.exit("ERROR: OPIK_API_KEY and OPIK_WORKSPACE must be set.")

    # Collect global assertions and policy from starters if available
    global_assertions: list[str] = []
    global_execution_policy: dict = {}

    all_items: list[dict] = []

    if args.from_github:
        items, _owner, _name = items_from_github(args.from_github)
        all_items.extend(items)

    if args.from_starter:
        items, global_assertions, global_execution_policy = items_from_starter()
        all_items.extend(items)

    client = opik.Opik(project_name=SUITE_PROJECT)
    suite = _get_or_create_suite(client, global_assertions, global_execution_policy)
    suite.insert(all_items)
    logger.info("Inserted %d items into test suite %r.", len(all_items), SUITE_NAME)
    logger.info("View the suite in the Opik UI under project %r.", SUITE_PROJECT)


if __name__ == "__main__":
    main()
