#!/usr/bin/env python3
"""Seed an Opik Dataset with Scout triage scenarios.

Two sources can be combined in one run:

  1. --from-github PATH   Read a JSON file produced by fetch_github_issues.py and
                          wrap each issue as a "default" scenario spec. The spec
                          omits the "files" key so file access is delegated to real
                          GitHub at eval time (requires GITHUB_TOKEN).

  2. --from-starter       Insert the starter scenarios from evals/starter_scenarios.py
                          into a separate dataset (fully simulated, no network required).

Env vars:
    OPIK_API_KEY, OPIK_WORKSPACE       — required
    SCOUT_EVAL_OPIK_PROJECT            — project for the dataset (default: scout-eval)
    SCOUT_GITHUB_DATASET_NAME          — dataset name for --from-github (default: scout-triage-inputs)
    SCOUT_STARTER_DATASET_NAME         — dataset name for --from-starter (default: scout-starter-scenarios)
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

_repo_owner = os.environ.get("SCOUT_GITHUB_REPO_OWNER", "")
_repo_name = os.environ.get("SCOUT_GITHUB_REPO_NAME", "")
_default_project = f"scout:{_repo_owner}/{_repo_name}" if _repo_owner and _repo_name else "scout-eval"
DATASET_PROJECT = os.environ.get("SCOUT_EVAL_OPIK_PROJECT") or _default_project
GITHUB_DATASET_NAME = os.environ.get("SCOUT_GITHUB_DATASET_NAME", "scout-triage-inputs")
STARTER_DATASET_NAME = os.environ.get("SCOUT_STARTER_DATASET_NAME", "scout-starter-scenarios")


def _issue_to_dataset_item(issue: dict, owner: str, name: str) -> dict:
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
    }


def items_from_github(path: str) -> tuple[list[dict], str, str]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    owner = payload["repo"]["owner"]
    name = payload["repo"]["name"]
    items = [_issue_to_dataset_item(issue, owner, name) for issue in payload["issues"]]
    logger.info("Loaded %d issues from %s (%s/%s)", len(items), path, owner, name)
    return items, owner, name


def items_from_starter() -> list[dict]:
    from evals.utils.starter_scenarios import STARTER_SCENARIOS
    items = [{"description": s["description"], "data": s["data"]} for s in STARTER_SCENARIOS]
    logger.info("Loaded %d starter scenario items", len(items))
    return items


def _get_or_create_dataset(client: opik.Opik, name: str, description: str) -> opik.Dataset:
    try:
        dataset = client.get_dataset(name)
        logger.info("Found existing dataset %r — appending to it.", name)
        return dataset
    except Exception as e:
        logger.info("Dataset %r not found (%s) — creating it.", name, type(e).__name__)

    try:
        dataset = client.create_dataset(name=name, description=description)
        logger.info("Created dataset %r.", name)
        return dataset
    except Exception:
        dataset = client.get_dataset(name)
        logger.info("Fetched existing dataset %r after conflict.", name)
        return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-github", metavar="PATH", help="JSON file from fetch_github_issues.py"
    )
    parser.add_argument(
        "--from-starter",
        action="store_true",
        help="Insert the starter scenarios from evals/starter_scenarios.py",
    )
    args = parser.parse_args()

    if not args.from_github and not args.from_starter:
        parser.error("Specify at least one of --from-github or --from-starter")

    if not os.environ.get("OPIK_API_KEY") or not os.environ.get("OPIK_WORKSPACE"):
        sys.exit("ERROR: OPIK_API_KEY and OPIK_WORKSPACE must be set.")

    client = opik.Opik(project_name=DATASET_PROJECT)

    if args.from_github:
        items, owner, name = items_from_github(args.from_github)
        dataset = _get_or_create_dataset(
            client,
            GITHUB_DATASET_NAME,
            f"Real GitHub issues from {owner}/{name} used as inputs for Scout triage evaluation.",
        )
        dataset.insert(items)
        logger.info("Inserted %d items into dataset %r.", len(items), GITHUB_DATASET_NAME)

    if args.from_starter:
        items = items_from_starter()
        dataset = _get_or_create_dataset(
            client,
            STARTER_DATASET_NAME,
            "Synthetic starter scenarios for Scout triage evaluation (fully simulated, no network required).",
        )
        dataset.insert(items)
        logger.info("Inserted %d items into dataset %r.", len(items), STARTER_DATASET_NAME)

    logger.info("View datasets in the Opik UI under project %r.", DATASET_PROJECT)


if __name__ == "__main__":
    main()
