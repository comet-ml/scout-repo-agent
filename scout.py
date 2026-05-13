#!/usr/bin/env python3
"""Scout: GitHub issue triage agent powered by Claude."""
from __future__ import annotations

import json
import logging
import os
import sys
import time

import anthropic
import jwt
import opik
import requests
from dotenv import load_dotenv
from github import Github, GithubException
from opik.integrations.anthropic import track_anthropic

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _require(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise ValueError(f"Required environment variable {name!r} is not set")
    return val


ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
SCOUT_APP_ID = os.environ.get("SCOUT_APP_ID", "")
SCOUT_APP_PRIVATE_KEY = os.environ.get("SCOUT_APP_PRIVATE_KEY", "")
SCOUT_ESCALATION_TAG = os.environ.get("SCOUT_ESCALATION_TAG", "Escalated request")
OPIK_API_KEY = os.environ.get("OPIK_API_KEY", "")
OPIK_WORKSPACE = os.environ.get("OPIK_WORKSPACE", "")
MODEL = os.environ.get("SCOUT_MODEL", "claude-opus-4-5")
MAX_TOKENS = int(os.environ.get("SCOUT_MAX_TOKENS", "8096"))
MAX_ITERATIONS = 15


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


def _get_issue_number() -> int:
    """Resolve issue number from ISSUE_NUMBER or the GitHub Actions event payload."""
    if "ISSUE_NUMBER" in os.environ:
        return int(os.environ["ISSUE_NUMBER"])
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_path and os.path.isfile(event_path):
        with open(event_path) as f:
            event = json.load(f)
        return int(event["issue"]["number"])
    raise ValueError("Set ISSUE_NUMBER or run inside a GitHub Actions issues event")


REPO_OWNER, REPO_NAME = _get_repo_owner_name()
ISSUE_NUMBER = _get_issue_number()


# ---------------------------------------------------------------------------
# Opik setup
# ---------------------------------------------------------------------------

if OPIK_API_KEY and OPIK_WORKSPACE:
    opik.configure(
        api_key=OPIK_API_KEY,
        workspace=OPIK_WORKSPACE,
        force=True,
        automatic_approvals=True,
    )

_raw_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
client = track_anthropic(_raw_client, project_name="scout")


# ---------------------------------------------------------------------------
# GitHub App auth
# ---------------------------------------------------------------------------

def get_github_token() -> str:
    """Mint a GitHub App JWT and exchange it for an installation access token."""
    if not SCOUT_APP_ID or not SCOUT_APP_PRIVATE_KEY:
        raise ValueError("SCOUT_APP_ID and SCOUT_APP_PRIVATE_KEY are required")

    if os.path.isfile(SCOUT_APP_PRIVATE_KEY):
        with open(SCOUT_APP_PRIVATE_KEY) as f:
            private_key = f.read()
    else:
        private_key = SCOUT_APP_PRIVATE_KEY  # raw PEM content passed directly

    now = int(time.time())
    app_jwt = jwt.encode(
        {"iat": now - 60, "exp": now + 600, "iss": SCOUT_APP_ID},
        private_key,
        algorithm="RS256",
    )

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
    }

    resp = requests.get(
        "https://api.github.com/app/installations",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()

    for inst in resp.json():
        if inst["account"]["login"].lower() == REPO_OWNER.lower():
            resp = requests.post(
                f"https://api.github.com/app/installations/{inst['id']}/access_tokens",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            logger.info("GitHub App token obtained for installation under %s", REPO_OWNER)
            return resp.json()["token"]

    raise ValueError(f"No GitHub App installation found for owner: {REPO_OWNER}")


# ---------------------------------------------------------------------------
# GitHub globals (set in main before agent runs)
# ---------------------------------------------------------------------------

gh: Github
repo: object
issue: object


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@opik.track(type="tool", project_name="scout")
def search_issues(query: str, max_results: int = 10) -> list[dict]:
    """Search for issues in the repository matching a text query."""
    try:
        results = []
        search_query = f"repo:{REPO_OWNER}/{REPO_NAME} {query}"
        for item in list(gh.search_issues(search_query))[:max_results]:
            results.append({
                "number": item.number,
                "title": item.title,
                "state": item.state,
                "url": item.html_url,
                "body": (item.body or "")[:500],
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]


@opik.track(type="tool", project_name="scout")
def list_directory(path: str = "") -> list[str]:
    """List files and directories at a path in the repository."""
    try:
        contents = repo.get_contents(path)
        if not isinstance(contents, list):
            contents = [contents]
        return [
            f"{c.name}/" if c.type == "dir" else c.name
            for c in sorted(contents, key=lambda c: (c.type != "dir", c.name))
        ]
    except GithubException as e:
        return [f"Error: {e.data.get('message', str(e))}"]


@opik.track(type="tool", project_name="scout")
def get_file_contents(path: str) -> str:
    """Read the contents of a file in the repository."""
    if ".." in path:
        return "Error: path traversal not allowed"
    try:
        content = repo.get_contents(path)
        if isinstance(content, list):
            return "Error: that path is a directory — use list_directory instead"
        text = content.decoded_content.decode("utf-8", errors="replace")
        if len(text) > 8000:
            text = text[:8000] + "\n\n... [file truncated at 8000 chars]"
        return text
    except GithubException as e:
        return f"Error: {e.data.get('message', str(e))}"


@opik.track(type="tool", project_name="scout")
def apply_label(label_name: str) -> str:
    """Apply a label to the current issue."""
    try:
        issue.add_to_labels(label_name)
        return f"Label '{label_name}' applied to issue #{ISSUE_NUMBER}"
    except GithubException as e:
        return f"Error applying label '{label_name}': {e.data.get('message', str(e))} — ensure the label exists in the repo"


TOOL_DEFINITIONS = [
    {
        "name": "search_issues",
        "description": (
            "Search for issues in the repository by text query. "
            "Use this to find similar bugs, existing workarounds, duplicate reports, or prior discussions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — keywords, error messages, feature names, etc.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a path in the repository. Use this to navigate the codebase before reading files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path. Use empty string for the repo root.",
                    "default": "",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_file_contents",
        "description": "Read the full contents of a source file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path within the repository (e.g. 'src/foo/bar.py')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "apply_label",
        "description": (
            f"Apply a label to the current issue. "
            f"Only call this when the issue requires a major design decision or significant architectural change. "
            f"Use label name: \"{SCOUT_ESCALATION_TAG}\""
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label_name": {
                    "type": "string",
                    "description": f"Label name to apply. Should be \"{SCOUT_ESCALATION_TAG}\" for escalations.",
                },
            },
            "required": ["label_name"],
        },
    },
]


def dispatch_tool(name: str, inputs: dict) -> str:
    if name == "search_issues":
        return json.dumps(search_issues(**inputs), indent=2)
    if name == "list_directory":
        return json.dumps(list_directory(**inputs), indent=2)
    if name == "get_file_contents":
        return get_file_contents(**inputs)
    if name == "apply_label":
        return apply_label(**inputs)
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are Scout, an AI agent that triages GitHub issues for the {REPO_OWNER}/{REPO_NAME} repository.

For each issue you will:
1. SEARCH — use search_issues to find similar bugs, duplicate reports, existing workarounds, and relevant prior discussions.
2. INVESTIGATE — use list_directory and get_file_contents to locate where in the codebase the problem lives. Find the relevant files, classes, and functions.
3. RESPOND — based on what you find, write a structured comment (format below).

Escalation rule: if the issue requires a major design decision — architectural change, breaking API modification, significant cross-cutting scope — call apply_label("{SCOUT_ESCALATION_TAG}") BEFORE writing your comment, then explain the design complexity in the Next Steps section.

Your comment must follow this exact structure:

## Solution / Workaround
[If a solution or workaround exists: exact steps. If not: "No existing solution or workaround found."]

## Code Investigation
[The relevant files and functions where this issue lives. Quote key lines. Reference files as `path/to/file.py`. If the issue is not code-related, say so.]

## Next Steps
[One of:
- A concrete fix sketch: which file, which function, what to change
- Why this requires a design decision (if you escalated, mention it was tagged for team review)]

Be direct and technical. Link to related issues by number (e.g. #42). Do not be condescending.
"""


def build_initial_message(issue_data: dict) -> str:
    comments_text = ""
    if issue_data["comments"]:
        formatted = "\n\n".join(
            f"**@{c['author']}**: {c['body']}" for c in issue_data["comments"]
        )
        comments_text = f"\n\n---\n**Comments ({len(issue_data['comments'])}):**\n\n{formatted}"

    return (
        f"Issue #{issue_data['number']}: {issue_data['title']}\n\n"
        f"Reporter: @{issue_data['author']}\n"
        f"Labels: {', '.join(issue_data['labels']) or 'none'}\n"
        f"State: {issue_data['state']}\n\n"
        f"{issue_data['body'] or '(no description provided)'}"
        f"{comments_text}\n\n"
        "Please triage this issue."
    )


def get_issue_data(issue_obj) -> dict:
    comments = []
    for c in list(issue_obj.get_comments())[:20]:
        comments.append({"author": c.user.login, "body": (c.body or "")[:500]})
    return {
        "number": issue_obj.number,
        "title": issue_obj.title,
        "body": issue_obj.body or "",
        "state": issue_obj.state,
        "author": issue_obj.user.login,
        "labels": [lbl.name for lbl in issue_obj.labels],
        "comments": comments,
    }


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(issue_number: int) -> str:
    def _agent():
        issue_data = get_issue_data(issue)
        messages = [{"role": "user", "content": build_initial_message(issue_data)}]

        for iteration in range(MAX_ITERATIONS):
            logger.info("Iteration %d/%d", iteration + 1, MAX_ITERATIONS)

            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return "Scout completed without producing a text response."

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("  Tool: %s(%s)", block.name, list(block.input.keys()))
                        result = dispatch_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})

        return "Scout reached the iteration limit without completing analysis."

    tracked = opik.track(name=f"scout-issue-{issue_number}", project_name="scout")(_agent)
    return tracked()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global gh, repo, issue

    logger.info("Scout starting — issue #%d in %s/%s", ISSUE_NUMBER, REPO_OWNER, REPO_NAME)

    token = get_github_token()
    gh = Github(token)
    repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
    issue = repo.get_issue(ISSUE_NUMBER)

    logger.info("Issue: %s", issue.title)

    try:
        comment_text = run_agent(ISSUE_NUMBER)
        issue.create_comment(comment_text)
        logger.info("Comment posted to issue #%d", ISSUE_NUMBER)
    except Exception as e:
        logger.error("Scout failed: %s", e, exc_info=True)
        try:
            issue.create_comment(
                "Scout encountered an error while analyzing this issue and could not complete triage.\n\n"
                "Please review this issue manually."
            )
        except Exception:
            pass
        sys.exit(1)
    finally:
        if OPIK_API_KEY:
            opik.flush_tracker()


if __name__ == "__main__":
    main()
