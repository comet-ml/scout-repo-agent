"""GitHubProvider: real GitHub backend via PyGithub."""
from __future__ import annotations

from github import Github, GithubException


class GitHubProvider:
    def __init__(self, token: str, owner: str, name: str):
        self._gh = Github(token)
        self._repo = self._gh.get_repo(f"{owner}/{name}")
        self._owner = owner
        self._name = name

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def name(self) -> str:
        return self._name

    # ---- read ----

    def get_issue_data(self, issue_number: int) -> dict:
        issue = self._repo.get_issue(issue_number)
        comments = [
            {"author": c.user.login, "body": (c.body or "")[:500]}
            for c in list(issue.get_comments())[:20]
        ]
        return {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body or "",
            "state": issue.state,
            "author": issue.user.login,
            "labels": [lbl.name for lbl in issue.labels],
            "comments": comments,
        }

    def search_issues(self, query: str, max_results: int = 10) -> list[dict]:
        try:
            search_query = f"repo:{self._owner}/{self._name} {query}"
            return [
                {
                    "number": item.number,
                    "title": item.title,
                    "state": item.state,
                    "url": item.html_url,
                    "body": (item.body or "")[:500],
                }
                for item in list(self._gh.search_issues(search_query))[:max_results]
            ]
        except Exception as e:
            return [{"error": str(e)}]

    def list_directory(self, path: str = "") -> list[str]:
        try:
            contents = self._repo.get_contents(path)
            if not isinstance(contents, list):
                contents = [contents]
            return [
                f"{c.name}/" if c.type == "dir" else c.name
                for c in sorted(contents, key=lambda c: (c.type != "dir", c.name))
            ]
        except GithubException as e:
            return [f"Error: {e.data.get('message', str(e))}"]

    def get_file_contents(self, path: str) -> str:
        try:
            content = self._repo.get_contents(path)
            if isinstance(content, list):
                return "Error: that path is a directory — use list_directory instead"
            text = content.decoded_content.decode("utf-8", errors="replace")
            if len(text) > 8000:
                text = text[:8000] + "\n\n... [file truncated at 8000 chars]"
            return text
        except GithubException as e:
            return f"Error: {e.data.get('message', str(e))}"

    def fetch_readme(self) -> str | None:
        for candidate in ("README.md", "README.rst", "README.txt", "README"):
            try:
                content = self._repo.get_contents(candidate)
                if isinstance(content, list):
                    continue
                text = content.decoded_content.decode("utf-8", errors="replace")
                return text[:3000] + ("\n... [truncated]" if len(text) > 3000 else "")
            except GithubException:
                continue
        return None

    # ---- write ----

    def add_reaction(self, issue_number: int, reaction: str) -> None:
        self._repo.get_issue(issue_number).create_reaction(reaction)

    def apply_label(self, issue_number: int, label_name: str) -> str:
        issue = self._repo.get_issue(issue_number)
        try:
            issue.add_to_labels(label_name)
            return f"Label '{label_name}' applied to issue #{issue_number}"
        except GithubException as e:
            if e.status == 422:
                try:
                    self._repo.create_label(label_name, "e11d48")
                    issue.add_to_labels(label_name)
                    return f"Label '{label_name}' created and applied to issue #{issue_number}"
                except GithubException as e2:
                    return f"Error creating label '{label_name}': {e2.data.get('message', str(e2))}"
            return f"Error applying label '{label_name}': {e.data.get('message', str(e))}"

    def ensure_label(self, label_name: str, color: str = "e11d48") -> str:
        """Ensure a repository label exists, creating it if missing. Returns
        "exists" or "created". Used by setup tooling to provision the escalation
        tag ahead of time (apply_label also creates it lazily at triage time)."""
        for label in self._repo.get_labels():
            if label.name == label_name:
                return "exists"
        self._repo.create_label(label_name, color)
        return "created"

    def post_comment(self, issue_number: int, body: str) -> None:
        self._repo.get_issue(issue_number).create_comment(body)
