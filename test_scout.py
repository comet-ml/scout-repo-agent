"""Unit tests for scout.py."""
import json
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest
from github import GithubException

import scout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _github_exc(status: int, message: str = "error") -> GithubException:
    return GithubException(status, {"message": message}, None)


# ---------------------------------------------------------------------------
# _get_repo_owner_name
# ---------------------------------------------------------------------------

class TestGetRepoOwnerName:
    def test_explicit_env_vars(self):
        with patch.dict(os.environ, {
            "SCOUT_GITHUB_REPO_OWNER": "myorg",
            "SCOUT_GITHUB_REPO_NAME": "myrepo",
        }):
            owner, name = scout._get_repo_owner_name()
        assert owner == "myorg"
        assert name == "myrepo"

    def test_github_repository_fallback(self):
        with patch.dict(os.environ, {
            "SCOUT_GITHUB_REPO_OWNER": "",
            "SCOUT_GITHUB_REPO_NAME": "",
            "GITHUB_REPOSITORY": "fallback-org/fallback-repo",
        }):
            owner, name = scout._get_repo_owner_name()
        assert owner == "fallback-org"
        assert name == "fallback-repo"

    def test_raises_when_missing(self):
        with patch.dict(os.environ, {
            "SCOUT_GITHUB_REPO_OWNER": "",
            "SCOUT_GITHUB_REPO_NAME": "",
            "GITHUB_REPOSITORY": "",
        }):
            with pytest.raises(ValueError, match="SCOUT_GITHUB_REPO_OWNER"):
                scout._get_repo_owner_name()


# ---------------------------------------------------------------------------
# _get_issue_number
# ---------------------------------------------------------------------------

class TestGetIssueNumber:
    def test_from_env_var(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": "99"}):
            assert scout._get_issue_number() == 99

    def test_from_event_file(self, tmp_path):
        event = {"issue": {"number": 7}}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event))
        env = {k: v for k, v in os.environ.items() if k != "ISSUE_NUMBER"}
        env["GITHUB_EVENT_PATH"] = str(event_file)
        with patch.dict(os.environ, env, clear=True):
            assert scout._get_issue_number() == 7

    def test_empty_env_falls_through_to_event_file(self, tmp_path):
        # Workflows commonly pass `${{ github.event.inputs.foo }}`, which is the
        # empty string on triggers that don't carry that input. Empty must be
        # treated as unset so auto-detection from the event payload still works.
        event = {"issue": {"number": 11}}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event))
        with patch.dict(os.environ, {"ISSUE_NUMBER": "", "GITHUB_EVENT_PATH": str(event_file)}, clear=True):
            assert scout._get_issue_number() == 11

    def test_raises_when_missing(self):
        env = {k: v for k, v in os.environ.items() if k not in ("ISSUE_NUMBER", "GITHUB_EVENT_PATH")}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="ISSUE_NUMBER"):
                scout._get_issue_number()


# ---------------------------------------------------------------------------
# _load_system_prompt
# ---------------------------------------------------------------------------

class TestLoadSystemPrompt:
    """Scout always sources its system prompt from Opik. The local base prompt
    (env override > file > built-in default) only seeds Opik on first run."""

    def _call(self, client, **overrides):
        defaults = dict(
            SCOUT_SYSTEM_PROMPT_OVERRIDE="",
            SCOUT_PROMPT_FILE="",
            SCOUT_OPIK_PROMPT_NAME="scout-system-prompt",
            SCOUT_OPIK_PROMPT_VERSION="",
            REPO_OWNER="test-owner",
            REPO_NAME="test-repo",
            OPIK_PROJECT="scout:test-owner/test-repo",
            SCOUT_ESCALATION_TAG="Escalated request",
        )
        defaults.update(overrides)
        with patch.multiple("scout", **defaults):
            with patch("scout.opik.Opik", return_value=client):
                return scout._load_system_prompt()

    @staticmethod
    def _client(*, get_return=None, get_side_effect=None):
        """A mock Opik client. create_prompt echoes the text it was asked to store."""
        c = MagicMock()
        if get_side_effect is not None:
            c.get_prompt.side_effect = get_side_effect
        else:
            c.get_prompt.return_value = get_return
        c.create_prompt.side_effect = lambda **kw: MagicMock(prompt=kw["prompt"])
        return c

    # --- existing Opik prompt is used verbatim --------------------------------

    def test_existing_opik_prompt_used_verbatim(self):
        # Placeholders in the stored body are passed through untouched, and no
        # new prompt is created.
        client = self._client(get_return=MagicMock(prompt="Triage for $repo_owner/$repo_name."))
        result = self._call(client)
        assert result == "Triage for $repo_owner/$repo_name."
        client.create_prompt.assert_not_called()

    def test_get_prompt_scoped_to_project_and_version(self):
        client = self._client(get_return=MagicMock(prompt="x"))
        self._call(client, SCOUT_OPIK_PROMPT_VERSION="v3")
        client.get_prompt.assert_called_once_with(
            name="scout-system-prompt",
            version="v3",
            project_name="scout:test-owner/test-repo",
        )

    # --- bootstrap: prompt missing -> create from local base ------------------

    def test_creates_from_default_when_missing(self):
        client = self._client(get_return=None)
        result = self._call(client, REPO_OWNER="myorg", REPO_NAME="myrepo")
        # Created from the built-in default with placeholders fully resolved.
        assert "myorg/myrepo" in result
        assert "$repo_owner" not in result
        kwargs = client.create_prompt.call_args.kwargs
        assert kwargs["name"] == "scout-system-prompt"
        assert kwargs["project_name"] == "scout:test-owner/test-repo"
        assert "myorg/myrepo" in kwargs["prompt"]

    def test_default_seed_substitutes_escalation_tag(self):
        client = self._client(get_return=None)
        result = self._call(client, SCOUT_ESCALATION_TAG="needs-design")
        assert "needs-design" in result

    def test_default_seed_includes_rating_line(self):
        client = self._client(get_return=None)
        result = self._call(client)
        assert "rate my response" in result

    def test_env_override_seeds_creation_when_missing(self):
        client = self._client(get_return=None)
        result = self._call(
            client,
            SCOUT_SYSTEM_PROMPT_OVERRIDE="Custom prompt for $repo_owner.",
            REPO_OWNER="acme",
        )
        assert result == "Custom prompt for acme."
        assert client.create_prompt.call_args.kwargs["prompt"] == "Custom prompt for acme."

    def test_file_seeds_creation_when_missing(self, tmp_path):
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("You are a bot for $repo_name.")
        client = self._client(get_return=None)
        result = self._call(client, SCOUT_PROMPT_FILE=str(prompt_file), REPO_NAME="widgets")
        assert result == "You are a bot for widgets."

    def test_env_override_takes_precedence_over_file_as_seed(self, tmp_path):
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("File prompt.")
        client = self._client(get_return=None)
        result = self._call(
            client,
            SCOUT_SYSTEM_PROMPT_OVERRIDE="Env prompt.",
            SCOUT_PROMPT_FILE=str(prompt_file),
        )
        assert result == "Env prompt."

    def test_seed_leaves_unknown_placeholders(self):
        client = self._client(get_return=None)
        result = self._call(
            client,
            SCOUT_SYSTEM_PROMPT_OVERRIDE='Reply with {"key": "value"} for $repo_owner.',
            REPO_OWNER="acme",
        )
        assert '{"key": "value"}' in result
        assert "acme" in result

    # --- resilience: Opik errors fall back to the local base prompt -----------

    def test_get_prompt_failure_falls_back_to_base(self):
        client = self._client(get_side_effect=RuntimeError("network down"))
        result = self._call(client, REPO_OWNER="myorg", REPO_NAME="myrepo")
        assert "myorg/myrepo" in result
        client.create_prompt.assert_not_called()

    def test_create_failure_falls_back_to_base(self):
        client = self._client(get_return=None)
        client.create_prompt.side_effect = RuntimeError("boom")
        result = self._call(client, REPO_OWNER="myorg", REPO_NAME="myrepo")
        assert "myorg/myrepo" in result


# ---------------------------------------------------------------------------
# apply_label
# ---------------------------------------------------------------------------

class TestApplyLabel:
    def setup_method(self):
        self.mock_issue = MagicMock()
        self.mock_repo = MagicMock()

    def _call(self, label_name: str) -> str:
        with patch.multiple("scout", create=True, issue=self.mock_issue, repo=self.mock_repo, ISSUE_NUMBER=42):
            return scout.apply_label(label_name)

    def test_success(self):
        result = self._call("bug")
        self.mock_issue.add_to_labels.assert_called_once_with("bug")
        assert "applied" in result

    def test_creates_label_on_422(self):
        self.mock_issue.add_to_labels.side_effect = [_github_exc(422), None]
        result = self._call("Escalated request")
        self.mock_repo.create_label.assert_called_once_with("Escalated request", "e11d48")
        assert "created and applied" in result

    def test_returns_error_when_create_also_fails(self):
        self.mock_issue.add_to_labels.side_effect = _github_exc(422)
        self.mock_repo.create_label.side_effect = _github_exc(403, "Forbidden")
        result = self._call("Escalated request")
        assert "Error creating label" in result
        assert "Forbidden" in result

    def test_non_422_error_returned_as_message(self):
        self.mock_issue.add_to_labels.side_effect = _github_exc(500, "Server error")
        result = self._call("bug")
        assert "Error applying label" in result
        assert "Server error" in result
        self.mock_repo.create_label.assert_not_called()


# ---------------------------------------------------------------------------
# get_file_contents
# ---------------------------------------------------------------------------

class TestGetFileContents:
    def setup_method(self):
        self.mock_repo = MagicMock()

    def _call(self, path: str) -> str:
        with patch.multiple("scout", create=True, repo=self.mock_repo):
            return scout.get_file_contents(path)

    def test_blocks_path_traversal(self):
        result = self._call("../../etc/passwd")
        assert "not allowed" in result
        self.mock_repo.get_contents.assert_not_called()

    def test_directory_path_returns_error(self):
        self.mock_repo.get_contents.return_value = [MagicMock(), MagicMock()]
        result = self._call("src/")
        assert "directory" in result

    def test_file_contents_returned(self):
        mock_content = MagicMock()
        mock_content.decoded_content = b"print('hello')"
        self.mock_repo.get_contents.return_value = mock_content
        result = self._call("src/main.py")
        assert result == "print('hello')"

    def test_long_file_truncated(self):
        mock_content = MagicMock()
        mock_content.decoded_content = ("x" * 9000).encode()
        self.mock_repo.get_contents.return_value = mock_content
        result = self._call("big.py")
        assert len(result) < 9000
        assert "truncated" in result

    def test_github_exception_returned_as_message(self):
        self.mock_repo.get_contents.side_effect = _github_exc(404, "Not Found")
        result = self._call("missing.py")
        assert "Error" in result
        assert "Not Found" in result


# ---------------------------------------------------------------------------
# build_repo_context
# ---------------------------------------------------------------------------

class TestBuildRepoContext:
    def test_contains_repo_tree(self):
        result = scout.build_repo_context(["src/", "README.md"], None)
        assert "src/" in result
        assert "README.md" in result

    def test_contains_readme(self):
        result = scout.build_repo_context(None, "This project does X.")
        assert "This project does X." in result

    def test_contains_both(self):
        result = scout.build_repo_context(["src/"], "My README.")
        assert "src/" in result
        assert "My README." in result

    def test_empty_when_both_none(self):
        result = scout.build_repo_context(None, None)
        assert result == ""


# ---------------------------------------------------------------------------
# build_issue_message
# ---------------------------------------------------------------------------

class TestBuildIssueMessage:
    def _issue(self, **overrides):
        base = {
            "number": 1,
            "title": "Something broke",
            "author": "user1",
            "labels": [],
            "state": "open",
            "body": "It does not work.",
            "comments": [],
        }
        base.update(overrides)
        return base

    def test_contains_title_and_body(self):
        msg = scout.build_issue_message(self._issue())
        assert "Something broke" in msg
        assert "It does not work." in msg

    def test_contains_comments(self):
        issue = self._issue(comments=[{"author": "alice", "body": "Me too!"}])
        msg = scout.build_issue_message(issue)
        assert "alice" in msg
        assert "Me too!" in msg

    def test_does_not_contain_repo_tree_or_readme(self):
        msg = scout.build_issue_message(self._issue())
        assert "Repository root" not in msg
        assert "Repository README" not in msg

    def test_labels_shown(self):
        msg = scout.build_issue_message(self._issue(labels=["bug", "help wanted"]))
        assert "bug" in msg
        assert "help wanted" in msg

    def test_no_labels_shows_none(self):
        msg = scout.build_issue_message(self._issue(labels=[]))
        assert "none" in msg
