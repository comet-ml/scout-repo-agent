#!/usr/bin/env python3
"""Seed an Opik Dataset with Scout triage scenarios using the simulator methodology.

Each item uses the same scenario/spec/target_issue shape that scout_eval.py and
run_offline_eval.py consume — fully reproducible, no live GitHub file reads required.

Two sources for items:

  1. --from-csv PATH   Read a CSV produced by fetch_test_issues.py and wrap each
                       issue as a "default" scenario spec. Since CSV snapshots
                       contain no repo files, the spec omits the "files" key and
                       relies on real-GitHub file access at eval time (real-GitHub
                       mode — requires GITHUB_TOKEN, SCOUT_GITHUB_REPO_OWNER,
                       SCOUT_GITHUB_REPO_NAME).

  2. --from-starter    Insert the same STARTER_SCENARIOS used by the Test Suite
                       (fully simulated, no network required).

Both flags may be combined to seed a single dataset from both sources.

Env vars:
    OPIK_API_KEY, OPIK_WORKSPACE       — required
    SCOUT_OFFLINE_DATASET_NAME         — dataset name (default: scout-test-issues)
    SCOUT_EVAL_OPIK_PROJECT            — project (default: scout-eval)
    SCOUT_GITHUB_REPO_OWNER            — required when using --from-csv
    SCOUT_GITHUB_REPO_NAME             — required when using --from-csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import opik
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATASET_NAME = os.environ.get("SCOUT_OFFLINE_DATASET_NAME", "scout-test-issues")
DATASET_PROJECT = os.environ.get("SCOUT_EVAL_OPIK_PROJECT", "scout-eval")
REPO_OWNER = os.environ.get("SCOUT_GITHUB_REPO_OWNER", "").strip()
REPO_NAME = os.environ.get("SCOUT_GITHUB_REPO_NAME", "").strip()


def _csv_row_to_dataset_item(row: dict) -> dict:
    """Convert a fetch_test_issues.py CSV row into a simulator-style dataset item.

    The resulting spec omits "files", so the default scenario builder uses
    real-GitHub mode for file access. Issues and search remain simulated using
    the captured snapshot.
    """
    try:
        comments = json.loads(row.get("comments_json") or "[]")
    except json.JSONDecodeError:
        comments = []

    labels_raw = row.get("labels", "")
    labels = [s.strip() for s in labels_raw.split(",") if s.strip()] if labels_raw else []

    issue_number = int(row["number"])

    return {
        "scenario": "default",
        "spec": {
            "owner": REPO_OWNER,
            "name": REPO_NAME,
            # No "files" key → real-GitHub mode for list_directory/get_file_contents
            "issues": [
                {
                    "number": issue_number,
                    "title": row.get("title", ""),
                    "body": row.get("body", ""),
                    "state": row.get("state", "open"),
                    "author": row.get("author", "unknown"),
                    "labels": labels,
                    "comments": comments,
                }
            ],
        },
        "target_issue": issue_number,
    }


def items_from_csv(path: str) -> list[dict]:
    if not REPO_OWNER or not REPO_NAME:
        sys.exit(
            "ERROR: --from-csv requires SCOUT_GITHUB_REPO_OWNER and "
            "SCOUT_GITHUB_REPO_NAME to be set in .env"
        )
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    items = [_csv_row_to_dataset_item(row) for row in rows]
    logger.info("Loaded %d items from %s", len(items), path)
    return items


def items_from_starter() -> list[dict]:
    from evals.starter_scenarios import STARTER_SCENARIOS
    items = [{"description": s["description"], "data": s["data"]} for s in STARTER_SCENARIOS]
    logger.info("Loaded %d starter scenario items", len(items))
    return items


def _get_or_create_dataset(client: opik.Opik) -> opik.Dataset:
    try:
        dataset = client.get_dataset(DATASET_NAME)
        logger.info("Found existing dataset %r — appending to it.", DATASET_NAME)
        return dataset
    except Exception as e:
        logger.info("Dataset %r not found (%s) — creating it.", DATASET_NAME, type(e).__name__)

    dataset = client.create_dataset(
        name=DATASET_NAME,
        description=(
            "Scout triage scenarios using the simulator methodology. "
            "Each item has scenario/spec/target_issue fields consumed by "
            "run_offline_eval.py and scout_eval.py."
        ),
    )
    logger.info("Created dataset %r.", DATASET_NAME)
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-csv", metavar="PATH", help="CSV file from fetch_test_issues.py")
    parser.add_argument(
        "--from-starter",
        action="store_true",
        help="Also insert the starter scenarios from evals/starter_scenarios.py",
    )
    args = parser.parse_args()

    if not args.from_csv and not args.from_starter:
        parser.error("Specify at least one of --from-csv or --from-starter")

    if not os.environ.get("OPIK_API_KEY") or not os.environ.get("OPIK_WORKSPACE"):
        sys.exit("ERROR: OPIK_API_KEY and OPIK_WORKSPACE must be set.")

    all_items: list[dict] = []
    if args.from_csv:
        all_items.extend(items_from_csv(args.from_csv))
    if args.from_starter:
        all_items.extend(items_from_starter())

    client = opik.Opik()
    dataset = _get_or_create_dataset(client)
    dataset.insert(all_items)
    logger.info("Inserted %d items into dataset %r.", len(all_items), DATASET_NAME)
    logger.info("View the dataset in the Opik UI under project %r.", DATASET_PROJECT)


if __name__ == "__main__":
    main()