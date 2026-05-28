#!/usr/bin/env python3
"""Seed the Opik Test Suite with the starter Scout triage scenarios.

Idempotent on the suite NAME: if a suite with that name already exists, this
script reuses it. Items are inserted regardless — re-running may produce
duplicates. To start clean, delete the suite in the Opik UI before re-seeding.

Env vars:
    OPIK_API_KEY, OPIK_WORKSPACE     — required, same as scout.py
    SCOUT_TEST_SUITE_NAME            — suite name (default: scout-triage-regression)
    SCOUT_EVAL_OPIK_PROJECT          — project for the suite (default: scout-eval)
"""
from __future__ import annotations

import logging
import os
import sys

import opik

from .starter_scenarios import (
    GLOBAL_ASSERTIONS,
    GLOBAL_EXECUTION_POLICY,
    STARTER_SCENARIOS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


SUITE_NAME = os.environ.get("SCOUT_TEST_SUITE_NAME", "scout-triage-regression")
SUITE_PROJECT = os.environ.get("SCOUT_EVAL_OPIK_PROJECT", "scout-eval")
SUITE_DESCRIPTION = (
    "Regression scenarios for the Scout GitHub issue triage agent. "
    "Each item populates a simulated GitHub via providers/scenarios.py and "
    "runs the agent end-to-end; assertions are LLM-judged."
)


def _get_or_create_suite(client: opik.Opik):
    """Return an existing suite by name, or create one if it doesn't exist."""
    try:
        suite = client.get_test_suite(name=SUITE_NAME, project_name=SUITE_PROJECT)
        logger.info("Found existing test suite %r — reusing.", SUITE_NAME)
        return suite
    except Exception as e:
        # 404 is the typical "not found" path, but Opik may raise other ApiError
        # shapes. Try create; if THAT fails, surface the error.
        logger.info("Test suite %r not found (%s) — creating it.", SUITE_NAME, type(e).__name__)

    suite = client.create_test_suite(
        name=SUITE_NAME,
        description=SUITE_DESCRIPTION,
        global_assertions=GLOBAL_ASSERTIONS,
        global_execution_policy=GLOBAL_EXECUTION_POLICY,
        project_name=SUITE_PROJECT,
    )
    logger.info("Created test suite %r in project %r.", SUITE_NAME, SUITE_PROJECT)
    return suite


def main() -> None:
    if not os.environ.get("OPIK_API_KEY") or not os.environ.get("OPIK_WORKSPACE"):
        print("ERROR: OPIK_API_KEY and OPIK_WORKSPACE must be set.", file=sys.stderr)
        sys.exit(2)

    client = opik.Opik()
    suite = _get_or_create_suite(client)

    logger.info("Inserting %d starter scenarios...", len(STARTER_SCENARIOS))
    suite.insert(STARTER_SCENARIOS)
    logger.info("Done. View the suite in the Opik UI under project %r.", SUITE_PROJECT)


if __name__ == "__main__":
    main()
