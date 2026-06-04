#!/usr/bin/env python3
"""Provision and verify Scout's setup for a repository.

Run this once when onboarding a repo (or any time you want to check the
configuration). It:

  1. Ensures the Opik chat prompt exists — creating it from the built-in base
     prompt, or migrating a legacy text prompt to a chat prompt. It never
     overwrites an existing prompt: edits live in the Opik UI.
  2. Ensures the escalation label exists on the GitHub repository.
  3. Reports the configured values and the resulting state.

Usage:
    scout-init            # provision (steps 1 & 2), then report
    scout-init --check    # report only; make no changes

Configuration comes from the same environment variables as the triage module
(SCOUT_GITHUB_REPO_OWNER/NAME, OPIK_API_KEY/WORKSPACE, GITHUB_TOKEN,
SCOUT_OPIK_PROMPT_NAME, SCOUT_ESCALATION_TAG, ...). ISSUE_NUMBER is not needed.
"""
from __future__ import annotations

import argparse
import sys

import opik
from opik.exceptions import PromptTemplateStructureMismatch

from scout import triage
from scout.providers.github import GitHubProvider


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def ensure_prompt(client: opik.Opik) -> str:
    """Ensure the Opik chat prompt exists. Returns the action taken:
    "exists", "created", or "migrated". Never updates an existing prompt."""
    try:
        chat = client.get_chat_prompt(
            name=triage.SCOUT_OPIK_PROMPT_NAME,
            project_name=triage.OPIK_PROJECT,
        )
    except PromptTemplateStructureMismatch:
        # A legacy text prompt exists under this name — convert it to a chat prompt.
        triage._migrate_text_prompt_to_chat(client)
        return "migrated"

    if chat is not None:
        return "exists"

    client.create_chat_prompt(
        name=triage.SCOUT_OPIK_PROMPT_NAME,
        messages=triage._system_messages(triage._base_system_prompt()),
        project_name=triage.OPIK_PROJECT,
    )
    return "created"


def ensure_escalation_label(provider: GitHubProvider) -> str:
    """Ensure the escalation label exists on the repo. Returns "exists" or
    "created"."""
    return provider.ensure_label(triage.SCOUT_ESCALATION_TAG)


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------

def _mask(value: str) -> str:
    if not value:
        return "MISSING"
    return f"set (…{value[-4:]})" if len(value) >= 4 else "set"


def _prompt_status(client: opik.Opik) -> str:
    """Human-readable description of the prompt's current state in Opik."""
    try:
        version = client.rest_client.prompts.retrieve_prompt_version(
            name=triage.SCOUT_OPIK_PROMPT_NAME,
            project_name=triage.OPIK_PROJECT,
        )
    except Exception:
        return "not found"
    structure = version.template_structure or "untyped"
    note = " (needs migration)" if structure != "chat" else ""
    return f"{structure}{note}, version {version.commit}"


def _label_status(provider: GitHubProvider | None) -> str:
    if provider is None:
        return "unknown (repo not accessible)"
    try:
        for label in provider._repo.get_labels():
            if label.name == triage.SCOUT_ESCALATION_TAG:
                return "present"
        return "missing"
    except Exception as e:
        return f"unknown ({e})"


def print_report(client: opik.Opik, provider: GitHubProvider | None, repo_error: str | None) -> None:
    def kv(label: str, value: object) -> None:
        print(f"  {label:<20}: {value}")

    print(f"\n=== Scout configuration for {triage.OPIK_PROJECT} ===\n")

    print("Configuration")
    kv("Repository", f"{triage.REPO_OWNER}/{triage.REPO_NAME}")
    kv("Opik workspace", triage.OPIK_WORKSPACE)
    kv("Opik project", triage.OPIK_PROJECT)
    kv("Prompt name", triage.SCOUT_OPIK_PROMPT_NAME)
    kv("Prompt version pin", triage.SCOUT_OPIK_PROMPT_VERSION or "latest")
    kv("Escalation tag", triage.SCOUT_ESCALATION_TAG)
    kv("Model", triage.MODEL)
    kv("Max tokens", triage.MAX_TOKENS)

    print("\nCredentials")
    kv("ANTHROPIC_API_KEY", _mask(triage.ANTHROPIC_API_KEY))
    kv("GITHUB_TOKEN", _mask(triage.GITHUB_TOKEN))
    kv("OPIK_API_KEY", _mask(triage.OPIK_API_KEY))

    print("\nStatus")
    project_id = triage._get_opik_project_id()
    kv("Opik project", f"found (id={project_id})" if project_id else "not found")
    kv("Prompt", _prompt_status(client))
    kv("GitHub repo", "accessible" if provider is not None else f"ERROR: {repo_error}")
    kv("Escalation label", _label_status(provider))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision and verify Scout's setup.")
    parser.add_argument(
        "--check", action="store_true",
        help="Report status only; make no changes.",
    )
    args = parser.parse_args(argv)

    client = opik.Opik()

    provider: GitHubProvider | None = None
    repo_error: str | None = None
    try:
        provider = GitHubProvider(triage.GITHUB_TOKEN, triage.REPO_OWNER, triage.REPO_NAME)
    except Exception as e:
        repo_error = str(e)

    if not args.check:
        prompt_action = ensure_prompt(client)
        print(f"Prompt: {prompt_action}")

        if provider is not None:
            label_action = ensure_escalation_label(provider)
            print(f"Escalation label: {label_action}")
        else:
            print(f"Escalation label: skipped — repo not accessible ({repo_error})")

    print_report(client, provider, repo_error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
