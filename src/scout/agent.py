"""Agent loop: drives Claude through tool-calling using a RepositoryProvider.

No module globals — everything the agent needs is passed in. Both production
(scout.triage) and the Test Suite driver (evals/run_eval.py) call run_agent the same
way, just with different providers and clients.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

import anthropic
import opik
import opik.opik_context as opik_context
from opik.integrations.anthropic import track_anthropic

from scout.providers.base import RepositoryProvider

logger = logging.getLogger(__name__)


DEFAULT_MAX_ITERATIONS = 15
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 8096


def make_client(api_key: str, opik_project: str | None = None):
    """Build an Anthropic client, optionally wrapped with Opik tracing."""
    raw = anthropic.Anthropic(api_key=api_key)
    if opik_project:
        return track_anthropic(raw, project_name=opik_project)
    return raw


def make_tool_definitions(escalation_tag: str) -> list[dict]:
    """Anthropic tool schemas. Only the escalation tag string varies."""
    return [
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
                f"Use label name: \"{escalation_tag}\""
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label_name": {
                        "type": "string",
                        "description": f"Label name to apply. Should be \"{escalation_tag}\" for escalations.",
                    },
                },
                "required": ["label_name"],
            },
            "cache_control": {"type": "ephemeral"},
        },
    ]


def make_tools(
    provider: RepositoryProvider,
    issue_number: int,
    *,
    opik_project: str | None = None,
) -> dict[str, Callable]:
    """Bind tool callables to a provider. Each returns a string (JSON-encoded
    for list/dict results) — the model sees text in tool_result blocks."""

    def _track(fn):
        if opik_project:
            return opik.track(type="tool", project_name=opik_project)(fn)
        return fn

    @_track
    def search_issues(query: str, max_results: int = 10) -> str:
        return json.dumps(provider.search_issues(query, max_results), indent=2)

    @_track
    def list_directory(path: str = "") -> str:
        return json.dumps(provider.list_directory(path), indent=2)

    @_track
    def get_file_contents(path: str) -> str:
        if ".." in path:
            return "Error: path traversal not allowed"
        return provider.get_file_contents(path)

    @_track
    def apply_label(label_name: str) -> str:
        return provider.apply_label(issue_number, label_name)

    return {
        "search_issues": search_issues,
        "list_directory": list_directory,
        "get_file_contents": get_file_contents,
        "apply_label": apply_label,
    }


def build_repo_context(repo_tree: list[str] | None, readme: str | None) -> str:
    """The static repo context block injected as a cached system message."""
    parts = []
    if repo_tree:
        tree_lines = "\n".join(f"  {entry}" for entry in repo_tree)
        parts.append(f"Repository root:\n{tree_lines}")
    if readme:
        parts.append(f"Repository README:\n{readme}")
    return "\n\n".join(parts)


def build_issue_message(issue_data: dict) -> str:
    """The user turn containing only the issue itself."""
    comments_text = ""
    if issue_data["comments"]:
        formatted = "\n\n".join(
            f"**@{c['author']}**: {c['body']}" for c in issue_data["comments"]
        )
        comments_text = (
            f"\n\n---\n**Comments ({len(issue_data['comments'])}):**\n\n{formatted}"
        )

    return (
        f"Issue #{issue_data['number']}: {issue_data['title']}\n\n"
        f"Reporter: @{issue_data['author']}\n"
        f"Labels: {', '.join(issue_data['labels']) or 'none'}\n"
        f"State: {issue_data['state']}\n\n"
        f"{issue_data['body'] or '(no description provided)'}"
        f"{comments_text}\n\n"
        "Please triage this issue."
    )


def run_agent(
    provider: RepositoryProvider,
    issue_number: int,
    *,
    client,
    system_prompt: str,
    escalation_tag: str,
    repo_owner: str,
    repo_name: str,
    opik_project: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> tuple[str, str | None]:
    """Run the agent loop against `provider`. Returns (comment_text, opik_trace_id|None)."""
    trace_id: list[str | None] = [None]

    tools = make_tools(provider, issue_number, opik_project=opik_project)
    tool_definitions = make_tool_definitions(escalation_tag)
    issue_data = provider.get_issue_data(issue_number)
    issue_message = build_issue_message(issue_data)

    def _agent(issue_message: str) -> str:
        repo_tree = provider.list_directory("")
        readme = provider.fetch_readme()
        repo_context = build_repo_context(repo_tree, readme)

        messages = [{"role": "user", "content": issue_message}]
        system = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": repo_context, "cache_control": {"type": "ephemeral"}},
        ]

        for iteration in range(max_iterations):
            logger.info("Iteration %d/%d", iteration + 1, max_iterations)

            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=tool_definitions,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                td = opik_context.get_current_trace_data()
                if td:
                    trace_id[0] = td.id
                    opik_context.update_current_trace(
                        thread_id=f"issue-{repo_owner}-{repo_name}-{issue_number}"
                    )
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return "Scout completed without producing a text response."

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("  Tool: %s(%s)", block.name, list(block.input.keys()))
                        result = tools[block.name](**block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})  # type: ignore[dict-item]

            remaining = max_iterations - iteration - 1
            if remaining == 5:
                messages.append({
                    "role": "user",
                    "content": (
                        f"[System: {remaining} tool-calling rounds remaining. "
                        "Stop exploring and begin writing your final response now.]"
                    ),
                })

        return "Scout reached the iteration limit without completing analysis."

    if opik_project:
        tracked = opik.track(
            name=f"scout-issue-{issue_number}",
            project_name=opik_project,
            tags=["scout-repo-agent"],
        )(_agent)
        text = tracked(issue_message)
    else:
        text = _agent(issue_message)

    return text, trace_id[0]
