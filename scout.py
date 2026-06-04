#!/usr/bin/env python3
"""Scout: GitHub issue triage agent powered by Claude.

Entry point. Parses env, configures Opik, loads the system prompt, builds a
GitHubProvider, and hands the agent loop to agent.run_agent.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from string import Template

import opik
import requests as _requests
from dotenv import load_dotenv
from opik.exceptions import PromptTemplateStructureMismatch

from agent import make_client, run_agent
from providers.github import GitHubProvider

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ValueError(f"Required environment variable {name!r} is not set")
    return val


ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
GITHUB_TOKEN = _require("GITHUB_TOKEN")
SCOUT_ESCALATION_TAG = os.environ.get("SCOUT_ESCALATION_TAG", "Escalated request").strip()
SCOUT_SYSTEM_PROMPT_OVERRIDE = os.environ.get("SCOUT_SYSTEM_PROMPT", "").strip()
SCOUT_PROMPT_FILE = os.environ.get("SCOUT_PROMPT_FILE", "").strip()
# Scout always sources its system prompt from Opik. SCOUT_OPIK_PROMPT_NAME names
# the prompt within the project; when unset it defaults to a standard name and is
# auto-created from the built-in base prompt on first run (see _load_system_prompt).
DEFAULT_OPIK_PROMPT_NAME = "scout-system-prompt"
SCOUT_OPIK_PROMPT_NAME = os.environ.get("SCOUT_OPIK_PROMPT_NAME", "").strip() or DEFAULT_OPIK_PROMPT_NAME
SCOUT_OPIK_PROMPT_VERSION = os.environ.get("SCOUT_OPIK_PROMPT_VERSION", "").strip()
# Opik is a hard requirement: Scout sources its prompt from Opik and traces there.
OPIK_API_KEY = _require("OPIK_API_KEY")
OPIK_WORKSPACE = _require("OPIK_WORKSPACE")
MODEL = os.environ.get("SCOUT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("SCOUT_MAX_TOKENS", "8096"))


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
    # Treat empty as unset: workflows often pass `${{ github.event.inputs.foo }}`,
    # which evaluates to "" on triggers that don't carry the input.
    issue_env = os.environ.get("ISSUE_NUMBER", "").strip()
    if issue_env:
        return int(issue_env)
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

# Opik is required; configuration failure is fatal.
try:
    opik.configure(
        api_key=OPIK_API_KEY,
        workspace=OPIK_WORKSPACE,
        force=True,
        automatic_approvals=True,
    )
except Exception as e:
    raise RuntimeError(f"Opik configuration failed (Opik is required): {e}") from e

_opik_enabled = True

OPIK_PROJECT = f"scout:{REPO_OWNER}/{REPO_NAME}"


def _get_opik_project_id() -> str | None:
    """Look up the Opik project UUID by name via REST API."""
    try:
        resp = _requests.get(
            "https://www.comet.com/opik/api/v1/private/projects",
            params={"page": "1", "size": "20", "name": OPIK_PROJECT},
            headers={"authorization": OPIK_API_KEY, "Comet-Workspace": OPIK_WORKSPACE},
            timeout=10,
        )
        for project in resp.json().get("content", []):
            if project["name"] == OPIK_PROJECT:
                return project["id"]
    except Exception as e:
        logger.debug("Could not fetch Opik project ID: %s", e)
    return None


def _feedback_marker(trace_id: str) -> str:
    """Hidden HTML marker stamped into Scout comments so the feedback sync job
    (scout_feedback.py) can map a comment's 👍/👎 reactions back to its Opik trace.
    Invisible in rendered GitHub markdown. Keep the format in sync with
    scout_feedback.MARKER_RE."""
    return f"<!-- scout-feedback trace_id={trace_id} -->"


def _build_opik_url(trace_id: str, project_id: str) -> str:
    return (
        f"https://www.comet.com/opik/{OPIK_WORKSPACE}/projects/{project_id}/logs"
        f"?time_range=alltime&traces_filters=%5B%5D&size=100&height=small"
        f"&trace={trace_id}&span=&trace_panel_filters=%5B%5D&thread="
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """\
You are Scout, an AI agent that triages GitHub issues for the $repo_owner/$repo_name repository.

Spam/off-topic rule: Before triaging, read the repository README provided in the issue context. If the issue is clearly spam, promotional content, or has nothing to do with the project described in the README (e.g. unrelated product pitches, generic requests, gibberish), skip the full triage and respond ONLY with:

Hi, I'm Scout 🦉, the $repo_owner/$repo_name repository agent.

This repository is for [one sentence describing the repo's purpose from the README]. This issue doesn't appear to be related to the project — a maintainer will review it shortly.

For each legitimate issue you will:
1. PLAN — before making any tool calls, write a 2–3 sentence investigation plan: which areas of the codebase are likely relevant and what files you expect to find. This prevents aimless exploration.
2. SEARCH — use search_issues to find similar bugs, duplicate reports, existing workarounds, and relevant prior discussions.
3. INVESTIGATE — use list_directory and get_file_contents to locate where in the codebase the problem lives. Find the relevant files, classes, and functions.
4. RESPOND — based on what you find, write a structured comment (format below).

Budget awareness: You have at most 15 tool-calling rounds. By round 10, stop exploring and begin writing your response using what you've found. An incomplete but substantive response is better than hitting the limit with no output.

Escalation rule: if the issue requires a major design decision — architectural change, breaking API modification, significant cross-cutting scope — call apply_label("$escalation_tag") BEFORE writing your comment, then explain the design complexity in the Next Steps section.

Your comment must follow this exact structure. Begin your response with the greeting line — do not write any text before it:

Hi, I'm Scout 🦉, the $repo_owner/$repo_name repository agent. Let me look into this.

## Solution / Workaround
[If a solution or workaround exists: exact steps. If not: "No existing solution or workaround found."]

## Code Investigation
[The relevant files and functions where this issue lives. Quote key lines. Reference files as `path/to/file.py`. If the issue is not code-related, say so.]

## Next Steps
[One of:
- A concrete fix sketch: which file, which function, what to change
- Why this requires a design decision (if you escalated, mention it was tagged for team review)]

[If your Next Steps include a concrete bug fix with specific code changes: add a final line such as "Feel free to open a pull request with this fix — the change is straightforward!"]

When investigating source code:
- The repository root contents are already in the issue context — do not call list_directory("") again.
- CRITICAL: Never list only a single directory per turn. Always batch at least 3 directory listings per turn, targeting the level where relevant files are likely to live (e.g., list the backend API handlers, frontend config pages, and test directories simultaneously). Descending one level at a time wastes your iteration budget.
- Issue 4–5 tool calls per turn wherever possible — parallelism is far more efficient than sequential exploration.
- Read all relevant source files in a single batched tool call rather than fetching them one at a time.
- Focus on code files directly relevant to the reported behavior — skip data files (word lists, configs, assets) unless the issue is specifically about that data.
- After identifying the relevant source, briefly check whether test coverage exists for the affected code (look in tests/ or similar) and note any gaps in your Code Investigation section.

Be direct and technical. Link to related issues by number (e.g. #42). Do not be condescending.

Finally, close every comment with this exact line, on its own line, as the last thing in your response:

_Was this helpful? React to this comment with 👍 or 👎 to rate my response._
"""


def _base_system_prompt() -> str:
    """The local 'seed' prompt used to bootstrap the Opik-managed prompt on first
    run: env override > prompt file > built-in default, with repo placeholders
    ($repo_owner / $repo_name / $escalation_tag) substituted so the text stored in
    Opik is fully resolved — no $-variables remain to expand."""
    if SCOUT_SYSTEM_PROMPT_OVERRIDE:
        raw = SCOUT_SYSTEM_PROMPT_OVERRIDE
    elif SCOUT_PROMPT_FILE:
        with open(SCOUT_PROMPT_FILE) as f:
            raw = f.read()
    else:
        raw = _DEFAULT_SYSTEM_PROMPT
    return Template(raw).safe_substitute(
        repo_owner=REPO_OWNER,
        repo_name=REPO_NAME,
        escalation_tag=SCOUT_ESCALATION_TAG,
    )


def _system_messages(text: str) -> list[dict[str, str]]:
    """Wrap the resolved system prompt text as a chat-prompt message list.

    Scout stores its system prompt as a single system-role message so the rest
    of the pipeline can keep treating the prompt as a plain string.
    """
    return [{"role": "system", "content": text}]


def _text_from_chat_prompt(chat) -> str:
    """Extract the system prompt text from an Opik ChatPrompt.

    Pulls the content of the system-role message (see _system_messages). Falls
    back to concatenating any other message content so we never return empty.
    """
    for message in chat.template:
        if message.get("role") == "system":
            return message["content"]
    return "\n\n".join(m.get("content", "") for m in chat.template)


def _migrate_text_prompt_to_chat(client) -> str:
    """Migrate a legacy text prompt to a chat prompt under the same name.

    Older Scout versions stored the system prompt as an Opik text prompt; Scout
    now needs a chat prompt. The template structure is immutable, so we copy the
    text, delete the text prompt, and recreate it as a chat prompt. Returns the
    migrated prompt text.
    """
    # Fetch via the REST layer: delete_prompt() deletes by *prompt* id, but the
    # high-level Prompt object's .id is the *version* id. retrieve_prompt_version
    # gives us both the template text and the prompt id in one call.
    version = client.rest_client.prompts.retrieve_prompt_version(
        name=SCOUT_OPIK_PROMPT_NAME,
        project_name=OPIK_PROJECT,
    )
    text = version.template
    logger.info(
        "Migrating Opik text prompt %r (id=%s) to a chat prompt in project %r",
        SCOUT_OPIK_PROMPT_NAME, version.prompt_id, OPIK_PROJECT,
    )
    client.rest_client.prompts.delete_prompt(id=version.prompt_id)
    client.create_chat_prompt(
        name=SCOUT_OPIK_PROMPT_NAME,
        messages=_system_messages(text),
        project_name=OPIK_PROJECT,
    )
    return text


def _load_system_prompt() -> str:
    """Always source the system prompt from Opik as a chat prompt.

    Fetch the chat prompt named SCOUT_OPIK_PROMPT_NAME from OPIK_PROJECT. If a
    legacy *text* prompt exists under that name, migrate it (copy, delete,
    recreate as a chat prompt). If nothing exists yet, create it from the local
    base prompt (see _base_system_prompt) so the built-in default is never used
    directly — it only seeds the first Opik version. The Opik body is used
    verbatim; edit it in the Opik UI to change Scout's behavior.
    """
    client = opik.Opik()
    version = SCOUT_OPIK_PROMPT_VERSION or None
    try:
        chat = client.get_chat_prompt(
            name=SCOUT_OPIK_PROMPT_NAME,
            version=version,
            project_name=OPIK_PROJECT,
        )
    except PromptTemplateStructureMismatch:
        # The name exists but points at a text prompt — migrate it to chat.
        try:
            return _migrate_text_prompt_to_chat(client)
        except Exception as e:
            logger.warning(
                "Failed to migrate Opik text prompt %r to a chat prompt: %s — "
                "using local base prompt",
                SCOUT_OPIK_PROMPT_NAME, e,
            )
            return _base_system_prompt()
    except Exception as e:
        logger.warning(
            "Failed to fetch Opik chat prompt %r (version=%r): %s — using local base prompt",
            SCOUT_OPIK_PROMPT_NAME, SCOUT_OPIK_PROMPT_VERSION or "latest", e,
        )
        return _base_system_prompt()

    if chat is not None:
        return _text_from_chat_prompt(chat)

    # First run for this project: seed Opik from the local base prompt.
    base = _base_system_prompt()
    logger.info(
        "Opik chat prompt %r not found in project %r — creating it from the base prompt",
        SCOUT_OPIK_PROMPT_NAME, OPIK_PROJECT,
    )
    try:
        client.create_chat_prompt(
            name=SCOUT_OPIK_PROMPT_NAME,
            messages=_system_messages(base),
            project_name=OPIK_PROJECT,
        )
        return base
    except Exception as e:
        logger.warning(
            "Failed to create Opik chat prompt %r: %s — using local base prompt",
            SCOUT_OPIK_PROMPT_NAME, e,
        )
        return base


SYSTEM_PROMPT = _load_system_prompt()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Scout starting — issue #%d in %s/%s", ISSUE_NUMBER, REPO_OWNER, REPO_NAME)

    provider = GitHubProvider(GITHUB_TOKEN, REPO_OWNER, REPO_NAME)
    provider.add_reaction(ISSUE_NUMBER, "eyes")

    issue_data = provider.get_issue_data(ISSUE_NUMBER)
    logger.info("Issue: %s", issue_data["title"])

    opik_project = OPIK_PROJECT if _opik_enabled else None
    client = make_client(ANTHROPIC_API_KEY, opik_project=opik_project)

    try:
        comment_text, opik_trace_id = run_agent(
            provider,
            ISSUE_NUMBER,
            client=client,
            system_prompt=SYSTEM_PROMPT,
            escalation_tag=SCOUT_ESCALATION_TAG,
            repo_owner=REPO_OWNER,
            repo_name=REPO_NAME,
            opik_project=opik_project,
            model=MODEL,
            max_tokens=MAX_TOKENS,
        )
        if opik_trace_id:
            opik.flush_tracker()  # ensure trace is written before we query for the project ID
            project_id = _get_opik_project_id()
            if project_id:
                opik_url = _build_opik_url(opik_trace_id, project_id)
                comment_text += f"\n\n---\n*[View Scout trace in Opik]({opik_url})*"
                logger.info("Opik trace: %s", opik_url)
            # Stamp the trace id so reaction-based feedback can be synced to Opik later.
            comment_text += f"\n\n{_feedback_marker(opik_trace_id)}"
        provider.post_comment(ISSUE_NUMBER, comment_text)
        logger.info("Comment posted to issue #%d", ISSUE_NUMBER)
    except Exception as e:
        logger.error("Scout failed: %s", e, exc_info=True)
        try:
            provider.post_comment(
                ISSUE_NUMBER,
                "Scout encountered an error while analyzing this issue and could not complete triage.\n\n"
                "Please review this issue manually."
            )
        except Exception:
            pass
        sys.exit(1)
    finally:
        if _opik_enabled:
            opik.flush_tracker()  # flush any remaining spans (tools, etc.)


if __name__ == "__main__":
    main()
