"""GitHubSimulator: in-memory GitHub backend for tests and Opik scenarios.

Behaves like the real API at the seams the agent touches: mutations from
apply_label/post_comment are visible to subsequent reads. Use the fluent
builder methods (add_issue, add_file, set_readme, set_search_handler) to
populate state, then hand the instance to the agent like any provider.

Optionally accepts an `upstream` RepositoryProvider. When set, file-side
reads (get_file_contents, list_directory, fetch_readme) delegate to it —
issues, search, and writes stay simulated. Scenarios use this to test
Scout against real repo code without embedding it in the spec.
"""
from __future__ import annotations

from typing import Callable

from scout.markers import SCOUT_COMMENT_MARKER

from .base import RepositoryProvider


def _comment_role(comment: dict) -> str:
    """Mirror GitHubProvider's role detection: a comment is an assistant turn if
    it explicitly says so, was authored by the simulated Scout bot, or carries
    Scout's hidden marker. Everything else is a human ``user`` turn."""
    if comment.get("role"):
        return comment["role"]
    if comment.get("author") == "scout-bot":
        return "assistant"
    if SCOUT_COMMENT_MARKER in (comment.get("body") or ""):
        return "assistant"
    return "user"


class GitHubSimulator:
    def __init__(
        self,
        owner: str = "sim",
        name: str = "repo",
        *,
        upstream: RepositoryProvider | None = None,
    ):
        self._owner = owner
        self._name = name
        self._issues: dict[int, dict] = {}
        self._files: dict[str, str] = {}
        self._readme: str | None = None
        self._upstream = upstream
        self._search_fn: Callable[[str, int, dict], list[dict]] = self._default_search
        self.calls: list[tuple] = []  # side-effect log for assertions

    # ---- properties ----

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def name(self) -> str:
        return self._name

    # ---- scenario building (fluent) ----

    def add_issue(
        self,
        number: int,
        *,
        title: str,
        body: str = "",
        state: str = "open",
        author: str = "user",
        author_association: str = "NONE",
        labels: list[str] | None = None,
        comments: list[dict] | None = None,
    ) -> "GitHubSimulator":
        # Comments are a flat, chronological list — GitHub issue comments are not
        # nested/threaded (that's PR review comments / Discussions). Order carries
        # the conversation. Each comment dict may carry an optional `association`
        # and `role`; both are normalized at read time in get_issue_data.
        self._issues[number] = {
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "author": author,
            "author_association": author_association,
            "labels": list(labels or []),
            "comments": [dict(c) for c in (comments or [])],
            "url": f"https://github.com/{self._owner}/{self._name}/issues/{number}",
        }
        return self

    def add_comment(
        self,
        issue_number: int,
        *,
        author: str,
        body: str,
        association: str = "NONE",
        role: str | None = None,
    ) -> "GitHubSimulator":
        """Append a comment to an existing issue. Use to build multi-party threads;
        pass role='assistant' (or author='scout-bot') for a prior Scout reply."""
        comment: dict = {"author": author, "body": body, "association": association}
        if role:
            comment["role"] = role
        self._issues[issue_number]["comments"].append(comment)
        return self

    def add_file(self, path: str, content: str) -> "GitHubSimulator":
        self._files[path] = content
        return self

    def set_readme(self, text: str) -> "GitHubSimulator":
        self._readme = text
        return self

    def set_search_handler(self, fn: Callable[[str, int, dict], list[dict]]) -> "GitHubSimulator":
        """Override the default substring matcher with custom behavior
        (flaky search, pagination quirks, results-after-N-calls, etc.)."""
        self._search_fn = fn
        return self

    # ---- state inspection (used by assertions) ----

    def issue(self, number: int) -> dict:
        """Current state of an issue, including labels Scout applied."""
        return self._issues[number]

    # ---- RepositoryProvider interface ----

    def get_issue_data(self, issue_number: int) -> dict:
        src = self._issues[issue_number]
        # Mirror GitHubProvider.get_issue_data exactly: keep the most recent 20
        # comments and truncate each body to 500 chars, so evals see the same
        # data production would.
        comments = []
        for c in src["comments"][-20:]:
            c = dict(c)
            c.setdefault("association", "NONE")
            c["role"] = _comment_role(c)
            c["body"] = (c.get("body") or "")[:500]
            comments.append(c)
        return {
            **src,
            "author_association": src.get("author_association", "NONE"),
            "labels": list(src["labels"]),
            "comments": comments,
        }

    def search_issues(self, query: str, max_results: int = 10) -> list[dict]:
        self.calls.append(("search_issues", query))
        return self._search_fn(query, max_results, self._issues)[:max_results]

    def list_directory(self, path: str = "") -> list[str]:
        if self._upstream is not None:
            return self._upstream.list_directory(path)
        prefix = f"{path}/" if path else ""
        seen = set()
        for p in self._files:
            if path and not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            head, sep, _ = rest.partition("/")
            seen.add(f"{head}/" if sep else head)
        return sorted(seen, key=lambda s: (not s.endswith("/"), s))

    def get_file_contents(self, path: str) -> str:
        if self._upstream is not None:
            return self._upstream.get_file_contents(path)
        if path not in self._files:
            return "Error: Not Found"
        text = self._files[path]
        if len(text) > 8000:
            text = text[:8000] + "\n\n... [file truncated at 8000 chars]"
        return text

    def fetch_readme(self) -> str | None:
        if self._upstream is not None:
            return self._upstream.fetch_readme()
        return self._readme

    def add_reaction(self, issue_number: int, reaction: str) -> None:
        self.calls.append(("add_reaction", issue_number, reaction))

    def apply_label(self, issue_number: int, label_name: str) -> str:
        if issue_number not in self._issues:
            return f"Error applying label '{label_name}': Not Found"
        if label_name not in self._issues[issue_number]["labels"]:
            self._issues[issue_number]["labels"].append(label_name)
        self.calls.append(("apply_label", issue_number, label_name))
        return f"Label '{label_name}' applied to issue #{issue_number}"

    def post_comment(self, issue_number: int, body: str) -> None:
        if issue_number in self._issues:
            self._issues[issue_number]["comments"].append(
                {"author": "scout-bot", "body": body}
            )
        self.calls.append(("post_comment", issue_number, body))

    # ---- default search ----

    @staticmethod
    def _default_search(query: str, max_results: int, issues: dict) -> list[dict]:
        """Substring match on title+body. Realistic enough to probe whether
        Scout formulates good search queries."""
        toks = [t for t in query.lower().split() if t]
        out = []
        for i in issues.values():
            hay = (i["title"] + " " + i["body"]).lower()
            if all(t in hay for t in toks):
                out.append({
                    "number": i["number"],
                    "title": i["title"],
                    "state": i["state"],
                    "url": i["url"],
                    "body": i["body"][:500],
                })
        return out
