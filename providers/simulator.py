"""GitHubSimulator: in-memory GitHub backend for tests and Opik scenarios.

Behaves like the real API at the seams the agent touches: mutations from
apply_label/post_comment are visible to subsequent reads. Use the fluent
builder methods (add_issue, add_file, set_readme, set_search_handler) to
populate state, then hand the instance to the agent like any provider.
"""
from __future__ import annotations

from typing import Callable


class GitHubSimulator:
    def __init__(self, owner: str = "sim", name: str = "repo"):
        self._owner = owner
        self._name = name
        self._issues: dict[int, dict] = {}
        self._files: dict[str, str] = {}
        self._readme: str | None = None
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
        labels: list[str] | None = None,
        comments: list[dict] | None = None,
    ) -> "GitHubSimulator":
        self._issues[number] = {
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "author": author,
            "labels": list(labels or []),
            "comments": list(comments or []),
            "url": f"https://github.com/{self._owner}/{self._name}/issues/{number}",
        }
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
        return {**src, "labels": list(src["labels"]), "comments": [dict(c) for c in src["comments"]]}

    def search_issues(self, query: str, max_results: int = 10) -> list[dict]:
        self.calls.append(("search_issues", query))
        return self._search_fn(query, max_results, self._issues)[:max_results]

    def list_directory(self, path: str = "") -> list[str]:
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
        if path not in self._files:
            return "Error: Not Found"
        text = self._files[path]
        if len(text) > 8000:
            text = text[:8000] + "\n\n... [file truncated at 8000 chars]"
        return text

    def fetch_readme(self) -> str | None:
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
