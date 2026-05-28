"""Unit tests for scout.py, agent.py, and providers/."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from github import GithubException

import agent
import scout
from evals.starter_scenarios import STARTER_SCENARIOS
from providers.github import GitHubProvider
from providers.scenarios import SCENARIO_BUILDERS, build
from providers.simulator import GitHubSimulator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _github_exc(status: int, message: str = "error") -> GithubException:
    return GithubException(status, {"message": message}, None)


def _make_github_provider() -> GitHubProvider:
    """A GitHubProvider with a mocked PyGithub backend — no constructor I/O."""
    p = GitHubProvider.__new__(GitHubProvider)
    p._gh = MagicMock()
    p._repo = MagicMock()
    p._owner = "test-owner"
    p._name = "test-repo"
    return p


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
    def _call(self, **overrides):
        defaults = dict(
            SCOUT_SYSTEM_PROMPT_OVERRIDE="",
            SCOUT_PROMPT_FILE="",
            SCOUT_OPIK_PROMPT_NAME="",
            SCOUT_OPIK_PROMPT_VERSION="",
            _opik_enabled=False,
            REPO_OWNER="test-owner",
            REPO_NAME="test-repo",
            OPIK_PROJECT="scout:test-owner/test-repo",
            SCOUT_ESCALATION_TAG="Escalated request",
        )
        defaults.update(overrides)
        with patch.multiple("scout", **defaults):
            return scout._load_system_prompt()

    def test_default_substitutes_owner_and_repo(self):
        result = self._call(REPO_OWNER="myorg", REPO_NAME="myrepo")
        assert "myorg/myrepo" in result

    def test_default_substitutes_escalation_tag(self):
        result = self._call(SCOUT_ESCALATION_TAG="needs-design")
        assert "needs-design" in result

    def test_env_override_used_when_set(self):
        result = self._call(
            SCOUT_SYSTEM_PROMPT_OVERRIDE="Custom prompt for $repo_owner.",
            REPO_OWNER="acme",
        )
        assert result == "Custom prompt for acme."

    def test_env_override_takes_precedence_over_file(self, tmp_path):
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("File prompt.")
        result = self._call(
            SCOUT_SYSTEM_PROMPT_OVERRIDE="Env prompt.",
            SCOUT_PROMPT_FILE=str(prompt_file),
        )
        assert result == "Env prompt."

    def test_file_used_when_no_env_override(self, tmp_path):
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("You are a bot for $repo_name.")
        result = self._call(
            SCOUT_PROMPT_FILE=str(prompt_file),
            REPO_NAME="widgets",
        )
        assert result == "You are a bot for widgets."

    def test_safe_substitute_leaves_unknown_placeholders(self):
        result = self._call(
            SCOUT_SYSTEM_PROMPT_OVERRIDE='Reply with {"key": "value"} for $repo_owner.',
            REPO_OWNER="acme",
        )
        assert '{"key": "value"}' in result
        assert "acme" in result

    def test_opik_prompt_used_when_configured(self):
        mock_prompt = MagicMock()
        mock_prompt.prompt = "Verbatim Opik body for acme/widgets."
        mock_client = MagicMock()
        mock_client.get_prompt.return_value = mock_prompt
        with patch("scout.opik.Opik", return_value=mock_client):
            result = self._call(
                SCOUT_OPIK_PROMPT_NAME="scout-prompt",
                _opik_enabled=True,
            )
        assert result == "Verbatim Opik body for acme/widgets."
        mock_client.get_prompt.assert_called_once_with(
            name="scout-prompt",
            version=None,
            project_name="scout:test-owner/test-repo",
        )
        mock_prompt.format.assert_not_called()

    def test_opik_prompt_returned_without_substitution(self):
        # The Opik body is used verbatim — placeholders like $repo_owner are
        # passed through to the model untouched.
        mock_prompt = MagicMock()
        mock_prompt.prompt = "Triage for $repo_owner/$repo_name."
        mock_client = MagicMock()
        mock_client.get_prompt.return_value = mock_prompt
        with patch("scout.opik.Opik", return_value=mock_client):
            result = self._call(
                SCOUT_OPIK_PROMPT_NAME="scout-prompt",
                _opik_enabled=True,
                REPO_OWNER="acme",
                REPO_NAME="widgets",
            )
        assert result == "Triage for $repo_owner/$repo_name."

    def test_opik_version_forwarded_when_set(self):
        mock_prompt = MagicMock()
        mock_prompt.prompt = "v3 prompt"
        mock_client = MagicMock()
        mock_client.get_prompt.return_value = mock_prompt
        with patch("scout.opik.Opik", return_value=mock_client):
            self._call(
                SCOUT_OPIK_PROMPT_NAME="scout-prompt",
                SCOUT_OPIK_PROMPT_VERSION="v3",
                _opik_enabled=True,
            )
        mock_client.get_prompt.assert_called_once_with(
            name="scout-prompt",
            version="v3",
            project_name="scout:test-owner/test-repo",
        )

    def test_opik_takes_precedence_over_env_override(self):
        mock_prompt = MagicMock()
        mock_prompt.prompt = "Opik wins"
        mock_client = MagicMock()
        mock_client.get_prompt.return_value = mock_prompt
        with patch("scout.opik.Opik", return_value=mock_client):
            result = self._call(
                SCOUT_OPIK_PROMPT_NAME="scout-prompt",
                _opik_enabled=True,
                SCOUT_SYSTEM_PROMPT_OVERRIDE="env prompt",
            )
        assert result == "Opik wins"

    def test_opik_fetch_failure_falls_back_to_default(self):
        mock_client = MagicMock()
        mock_client.get_prompt.side_effect = RuntimeError("network down")
        with patch("scout.opik.Opik", return_value=mock_client):
            result = self._call(
                SCOUT_OPIK_PROMPT_NAME="scout-prompt",
                _opik_enabled=True,
                REPO_OWNER="myorg",
                REPO_NAME="myrepo",
            )
        assert "myorg/myrepo" in result

    def test_opik_fetch_failure_falls_back_to_env_override(self):
        mock_client = MagicMock()
        mock_client.get_prompt.return_value = None  # treated as failure
        with patch("scout.opik.Opik", return_value=mock_client):
            result = self._call(
                SCOUT_OPIK_PROMPT_NAME="scout-prompt",
                _opik_enabled=True,
                SCOUT_SYSTEM_PROMPT_OVERRIDE="fallback env prompt",
            )
        assert result == "fallback env prompt"

    def test_opik_branch_skipped_when_disabled(self):
        with patch("scout.opik.Opik") as opik_ctor:
            result = self._call(
                SCOUT_OPIK_PROMPT_NAME="scout-prompt",
                _opik_enabled=False,
                REPO_OWNER="myorg",
                REPO_NAME="myrepo",
            )
        opik_ctor.assert_not_called()
        assert "myorg/myrepo" in result


# ---------------------------------------------------------------------------
# GitHubProvider.apply_label
# ---------------------------------------------------------------------------

class TestGitHubProviderApplyLabel:
    def setup_method(self):
        self.provider = _make_github_provider()
        self.mock_issue = MagicMock()
        self.provider._repo.get_issue.return_value = self.mock_issue

    def test_success(self):
        result = self.provider.apply_label(42, "bug")
        self.mock_issue.add_to_labels.assert_called_once_with("bug")
        assert "applied" in result

    def test_creates_label_on_422(self):
        self.mock_issue.add_to_labels.side_effect = [_github_exc(422), None]
        result = self.provider.apply_label(42, "Escalated request")
        self.provider._repo.create_label.assert_called_once_with("Escalated request", "e11d48")
        assert "created and applied" in result

    def test_returns_error_when_create_also_fails(self):
        self.mock_issue.add_to_labels.side_effect = _github_exc(422)
        self.provider._repo.create_label.side_effect = _github_exc(403, "Forbidden")
        result = self.provider.apply_label(42, "Escalated request")
        assert "Error creating label" in result
        assert "Forbidden" in result

    def test_non_422_error_returned_as_message(self):
        self.mock_issue.add_to_labels.side_effect = _github_exc(500, "Server error")
        result = self.provider.apply_label(42, "bug")
        assert "Error applying label" in result
        assert "Server error" in result
        self.provider._repo.create_label.assert_not_called()


# ---------------------------------------------------------------------------
# GitHubProvider.get_file_contents
# ---------------------------------------------------------------------------

class TestGitHubProviderGetFileContents:
    def setup_method(self):
        self.provider = _make_github_provider()

    def test_directory_path_returns_error(self):
        self.provider._repo.get_contents.return_value = [MagicMock(), MagicMock()]
        result = self.provider.get_file_contents("src/")
        assert "directory" in result

    def test_file_contents_returned(self):
        mock_content = MagicMock()
        mock_content.decoded_content = b"print('hello')"
        self.provider._repo.get_contents.return_value = mock_content
        result = self.provider.get_file_contents("src/main.py")
        assert result == "print('hello')"

    def test_long_file_truncated(self):
        mock_content = MagicMock()
        mock_content.decoded_content = ("x" * 9000).encode()
        self.provider._repo.get_contents.return_value = mock_content
        result = self.provider.get_file_contents("big.py")
        assert len(result) < 9000
        assert "truncated" in result

    def test_github_exception_returned_as_message(self):
        self.provider._repo.get_contents.side_effect = _github_exc(404, "Not Found")
        result = self.provider.get_file_contents("missing.py")
        assert "Error" in result
        assert "Not Found" in result


# ---------------------------------------------------------------------------
# build_repo_context  (moved to agent.py)
# ---------------------------------------------------------------------------

class TestBuildRepoContext:
    def test_contains_repo_tree(self):
        result = agent.build_repo_context(["src/", "README.md"], None)
        assert "src/" in result
        assert "README.md" in result

    def test_contains_readme(self):
        result = agent.build_repo_context(None, "This project does X.")
        assert "This project does X." in result

    def test_contains_both(self):
        result = agent.build_repo_context(["src/"], "My README.")
        assert "src/" in result
        assert "My README." in result

    def test_empty_when_both_none(self):
        result = agent.build_repo_context(None, None)
        assert result == ""


# ---------------------------------------------------------------------------
# build_issue_message  (moved to agent.py)
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
        msg = agent.build_issue_message(self._issue())
        assert "Something broke" in msg
        assert "It does not work." in msg

    def test_contains_comments(self):
        issue = self._issue(comments=[{"author": "alice", "body": "Me too!"}])
        msg = agent.build_issue_message(issue)
        assert "alice" in msg
        assert "Me too!" in msg

    def test_does_not_contain_repo_tree_or_readme(self):
        msg = agent.build_issue_message(self._issue())
        assert "Repository root" not in msg
        assert "Repository README" not in msg

    def test_labels_shown(self):
        msg = agent.build_issue_message(self._issue(labels=["bug", "help wanted"]))
        assert "bug" in msg
        assert "help wanted" in msg

    def test_no_labels_shows_none(self):
        msg = agent.build_issue_message(self._issue(labels=[]))
        assert "none" in msg


# ---------------------------------------------------------------------------
# GitHubSimulator
# ---------------------------------------------------------------------------

class TestGitHubSimulator:
    def test_get_issue_data_returns_added_issue(self):
        sim = GitHubSimulator().add_issue(7, title="bug", body="broken")
        data = sim.get_issue_data(7)
        assert data["number"] == 7
        assert data["title"] == "bug"
        assert data["body"] == "broken"
        assert data["labels"] == []
        assert data["state"] == "open"

    def test_get_issue_data_is_a_copy(self):
        # Callers shouldn't be able to mutate simulator state via the dict.
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.get_issue_data(7)["labels"].append("hacked")
        assert sim.issue(7)["labels"] == []

    def test_default_search_substring_match(self):
        sim = (
            GitHubSimulator()
            .add_issue(1, title="multi-gpu hang", body="all-reduce deadlock")
            .add_issue(2, title="docs typo", body="fix readme")
            .add_issue(3, title="cli flag parsing", body="multi-arg bug")
        )
        results = sim.search_issues("multi", 10)
        nums = {r["number"] for r in results}
        assert nums == {1, 3}

    def test_default_search_requires_all_tokens(self):
        sim = (
            GitHubSimulator()
            .add_issue(1, title="multi-gpu hang", body="all-reduce deadlock")
            .add_issue(2, title="single-gpu hang", body="something else")
        )
        results = sim.search_issues("multi gpu", 10)
        assert {r["number"] for r in results} == {1}

    def test_search_max_results_respected(self):
        sim = GitHubSimulator()
        for n in range(1, 6):
            sim.add_issue(n, title="hang", body="bug")
        results = sim.search_issues("hang", max_results=3)
        assert len(results) == 3

    def test_search_handler_swap(self):
        sim = GitHubSimulator().add_issue(1, title="t", body="b")
        sim.set_search_handler(lambda q, n, issues: [{"sentinel": True}])
        assert sim.search_issues("anything", 10) == [{"sentinel": True}]

    def test_list_directory_root(self):
        sim = (
            GitHubSimulator()
            .add_file("README.md", "x")
            .add_file("src/a.py", "x")
            .add_file("src/b.py", "x")
            .add_file("tests/t.py", "x")
        )
        entries = sim.list_directory("")
        assert "src/" in entries
        assert "tests/" in entries
        assert "README.md" in entries

    def test_list_directory_subpath(self):
        sim = (
            GitHubSimulator()
            .add_file("src/a.py", "x")
            .add_file("src/nested/b.py", "x")
        )
        entries = sim.list_directory("src")
        assert "a.py" in entries
        assert "nested/" in entries

    def test_get_file_contents_hit_and_miss(self):
        sim = GitHubSimulator().add_file("a.py", "print('hi')")
        assert sim.get_file_contents("a.py") == "print('hi')"
        assert "Not Found" in sim.get_file_contents("missing.py")

    def test_get_file_contents_truncates(self):
        sim = GitHubSimulator().add_file("big.py", "x" * 9000)
        result = sim.get_file_contents("big.py")
        assert "truncated" in result
        assert len(result) < 9000

    def test_apply_label_mutates_state(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.apply_label(7, "Escalated request")
        assert "Escalated request" in sim.issue(7)["labels"]
        assert ("apply_label", 7, "Escalated request") in sim.calls

    def test_apply_label_dedupes(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b", labels=["bug"])
        sim.apply_label(7, "bug")
        assert sim.issue(7)["labels"] == ["bug"]

    def test_apply_label_unknown_issue_returns_error(self):
        sim = GitHubSimulator()
        result = sim.apply_label(999, "bug")
        assert "Error" in result

    def test_post_comment_records_and_appends(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.post_comment(7, "hello")
        assert sim.issue(7)["comments"] == [{"author": "scout-bot", "body": "hello"}]
        assert ("post_comment", 7, "hello") in sim.calls

    def test_add_reaction_records_only(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.add_reaction(7, "eyes")
        assert ("add_reaction", 7, "eyes") in sim.calls

    def test_fetch_readme(self):
        sim = GitHubSimulator()
        assert sim.fetch_readme() is None
        sim.set_readme("# Repo")
        assert sim.fetch_readme() == "# Repo"


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

class TestScenarioBuilders:
    def test_default_builder_populates_simulator(self):
        spec = {
            "owner": "acme",
            "name": "widgets",
            "readme": "hello",
            "files": {"src/a.py": "code"},
            "issues": [
                {"number": 1, "title": "t", "body": "b"},
                {"number": 2, "title": "u", "body": "c"},
            ],
        }
        sim = build("default", spec)
        assert sim.owner == "acme"
        assert sim.name == "widgets"
        assert sim.fetch_readme() == "hello"
        assert sim.get_file_contents("src/a.py") == "code"
        assert sim.get_issue_data(1)["title"] == "t"
        assert sim.get_issue_data(2)["title"] == "u"

    def test_unknown_scenario_raises(self):
        with pytest.raises(ValueError, match="Unknown scenario"):
            build("does-not-exist", {})

    def test_search_rate_limited_drops_after_two_calls(self):
        spec = {"issues": [{"number": 1, "title": "hang", "body": "deadlock"}]}
        sim = build("search-rate-limited", spec)
        # First two calls hit the underlying default search
        assert sim.search_issues("hang", 10) != []
        assert sim.search_issues("hang", 10) != []
        # Third+ return empty
        assert sim.search_issues("hang", 10) == []
        assert sim.search_issues("hang", 10) == []

    def test_default_builder_registered(self):
        assert "default" in SCENARIO_BUILDERS


# ---------------------------------------------------------------------------
# make_tools
# ---------------------------------------------------------------------------

class TestMakeTools:
    def setup_method(self):
        self.sim = (
            GitHubSimulator()
            .add_issue(42, title="t", body="b")
            .add_file("src/a.py", "code")
            .add_issue(7, title="hang", body="multi-gpu deadlock")
        )
        self.tools = agent.make_tools(self.sim, issue_number=42)

    def test_search_issues_returns_json_string(self):
        result = self.tools["search_issues"]("hang", 10)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert parsed[0]["number"] == 7

    def test_list_directory_returns_json_string(self):
        result = self.tools["list_directory"]("")
        parsed = json.loads(result)
        assert "src/" in parsed

    def test_get_file_contents_returns_string(self):
        assert self.tools["get_file_contents"]("src/a.py") == "code"

    def test_get_file_contents_blocks_path_traversal(self):
        result = self.tools["get_file_contents"]("../../etc/passwd")
        assert "not allowed" in result

    def test_apply_label_uses_bound_issue_number(self):
        # The tool dispatch only passes label_name; issue number was bound at
        # make_tools() time.
        self.tools["apply_label"]("Escalated request")
        assert "Escalated request" in self.sim.issue(42)["labels"]


# ---------------------------------------------------------------------------
# Starter scenarios — each must build cleanly and have a triagable target
# ---------------------------------------------------------------------------

class TestStarterScenarios:
    @pytest.mark.parametrize("item", STARTER_SCENARIOS, ids=lambda i: i["description"])
    def test_scenario_has_required_keys(self, item):
        assert "description" in item
        assert "data" in item
        assert "assertions" in item and isinstance(item["assertions"], list) and item["assertions"]
        data = item["data"]
        for key in ("scenario_id", "scenario", "spec", "target_issue", "expected"):
            assert key in data, f"missing data key: {key}"

    @pytest.mark.parametrize("item", STARTER_SCENARIOS, ids=lambda i: i["description"])
    def test_scenario_builds_and_target_issue_resolvable(self, item):
        data = item["data"]
        sim = build(data["scenario"], data["spec"])
        target = data["target_issue"]
        # The simulator must know about the target issue — otherwise the agent
        # can't read it via get_issue_data.
        issue = sim.get_issue_data(target)
        assert issue["number"] == target

    @pytest.mark.parametrize("item", STARTER_SCENARIOS, ids=lambda i: i["description"])
    def test_scenario_uses_registered_builder(self, item):
        assert item["data"]["scenario"] in SCENARIO_BUILDERS
