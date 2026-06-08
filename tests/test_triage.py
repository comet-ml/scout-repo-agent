"""Unit tests for scout.triage, scout.agent, and scout.providers."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from github import GithubException

from evals.starter_scenarios import STARTER_SCENARIOS
from scout import agent
from scout import triage as scout
from scout.providers.github import GitHubProvider
from scout.providers.scenarios import SCENARIO_BUILDERS, build
from scout.providers.simulator import GitHubSimulator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _github_exc(status: int, message: str = "error") -> GithubException:
    return GithubException(status, {"message": message}, None)


def _label(name: str) -> MagicMock:
    """A mock GitHub Label. `.name` must be set after construction because
    MagicMock(name=...) sets the mock's repr name, not a `.name` attribute."""
    label = MagicMock()
    label.name = name
    return label


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

    def test_resolves_from_issue_comment_payload(self, tmp_path):
        # issue_comment events carry the issue number under the same key.
        event = {"issue": {"number": 314}, "comment": {"id": 1, "body": "hi"}}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(event))
        with patch.dict(os.environ, {"ISSUE_NUMBER": "", "GITHUB_EVENT_PATH": str(event_file)}, clear=True):
            assert scout._get_issue_number() == 314


# ---------------------------------------------------------------------------
# Comment-trigger gating
# ---------------------------------------------------------------------------

class TestCommentTriggerGating:
    def test_is_bot_by_type(self):
        assert scout._is_bot({"type": "Bot", "login": "x"}) is True

    def test_is_bot_by_login_suffix(self):
        assert scout._is_bot({"type": "User", "login": "github-actions[bot]"}) is True

    def test_is_bot_false_for_human(self):
        assert scout._is_bot({"type": "User", "login": "alice"}) is False

    def test_is_bot_false_for_empty(self):
        assert scout._is_bot({}) is False

    def test_mentions_scout(self):
        assert scout._mentions_scout("hey @Scout can you help") is True
        assert scout._mentions_scout("no mention here") is False

    def _event(self, body="real question", user=None, pull_request=False):
        issue = {"number": 1}
        if pull_request:
            issue["pull_request"] = {"url": "https://api.github.com/.../pulls/1"}
        return {
            "issue": issue,
            "comment": {"id": 9, "body": body, "user": user or {"login": "alice", "type": "User"}},
        }

    def test_skip_pull_request_comment(self):
        reason = scout._should_skip_comment_event(self._event(pull_request=True))
        assert reason and "pull request" in reason

    def test_skip_scouts_own_comment(self):
        body = "my reply <!-- scout-feedback trace_id=abc -->"
        reason = scout._should_skip_comment_event(self._event(body=body, user={"login": "scout[bot]", "type": "Bot"}))
        assert reason and "Scout's own" in reason

    def test_skip_bot_comment(self):
        reason = scout._should_skip_comment_event(
            self._event(user={"login": "dependabot[bot]", "type": "Bot"})
        )
        assert reason and "bot" in reason

    def test_proceeds_on_human_comment(self):
        assert scout._should_skip_comment_event(self._event()) is None

    def test_mention_gating_skips_when_unmentioned(self):
        with patch.object(scout, "SCOUT_COMMENT_TRIGGER_MENTION", True):
            reason = scout._should_skip_comment_event(self._event(body="no mention here"))
        assert reason and "mention" in reason

    def test_mention_gating_proceeds_when_mentioned(self):
        with patch.object(scout, "SCOUT_COMMENT_TRIGGER_MENTION", True):
            assert scout._should_skip_comment_event(self._event(body="@scout please look")) is None


# ---------------------------------------------------------------------------
# GitHub error messaging (permission / scope failures)
# ---------------------------------------------------------------------------

class TestGitHubErrorMessaging:
    def test_is_permission_error_true_for_403(self):
        assert scout._is_permission_error(_github_exc(403)) is True

    def test_is_permission_error_true_for_401(self):
        assert scout._is_permission_error(_github_exc(401)) is True

    def test_is_permission_error_false_for_404(self):
        assert scout._is_permission_error(_github_exc(404)) is False

    def test_is_permission_error_false_for_non_github_exception(self):
        assert scout._is_permission_error(RuntimeError("boom")) is False

    def test_explain_403_includes_actionable_permission_help(self):
        msg = scout._explain_github_error(_github_exc(403, "Resource not accessible by integration"))
        assert "issues: write" in msg
        assert "contents: read" in msg
        # The underlying GitHub detail and status are surfaced too.
        assert "Resource not accessible by integration" in msg
        assert "403" in msg

    def test_explain_non_permission_error_omits_help(self):
        msg = scout._explain_github_error(_github_exc(500, "Server error"))
        assert "issues: write" not in msg
        assert "500" in msg
        assert "Server error" in msg


# ---------------------------------------------------------------------------
# main(): GitHub setup failures degrade gracefully
# ---------------------------------------------------------------------------

class TestMainGitHubFailures:
    def test_react_best_effort_swallows_permission_error(self):
        provider = MagicMock()
        provider.add_reaction.side_effect = _github_exc(403)
        # Must not raise — reacting is not essential to triage.
        scout._react(provider, 42, None)

    def test_react_falls_back_to_issue_when_comment_reaction_fails(self):
        provider = MagicMock()
        provider.add_comment_reaction.side_effect = _github_exc(404)
        scout._react(provider, 42, 99)
        provider.add_reaction.assert_called_once_with(42, "eyes")

    def test_provider_construction_permission_error_exits_cleanly(self, caplog):
        # A 403 building the provider exits non-zero with the actionable message,
        # not an unhandled traceback.
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "", "ISSUE_NUMBER": "42"}):
            with patch("scout.triage.GitHubProvider", side_effect=_github_exc(403, "Resource not accessible by integration")):
                with pytest.raises(SystemExit) as exc:
                    scout.main()
        assert exc.value.code == 1
        assert "issues: write" in caplog.text

    def test_get_issue_data_permission_error_exits_cleanly(self, caplog):
        # Provider builds, the best-effort reaction is attempted, then reading the
        # issue fails with 403 → clean exit before the agent ever runs.
        provider = MagicMock()
        provider.get_issue_data.side_effect = _github_exc(403, "Resource not accessible by integration")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "", "ISSUE_NUMBER": "42"}):
            with patch("scout.triage.GitHubProvider", return_value=provider):
                with pytest.raises(SystemExit) as exc:
                    scout.main()
        assert exc.value.code == 1
        assert "issues: write" in caplog.text
        provider.add_reaction.assert_called_once()


# ---------------------------------------------------------------------------
# _load_system_prompt
# ---------------------------------------------------------------------------

class TestLoadSystemPrompt:
    """Scout always sources its system prompt from Opik as a chat prompt. The
    local base prompt (env override > file > built-in default) only seeds Opik
    on first run, and a legacy text prompt is migrated to a chat prompt."""

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
        with patch.multiple("scout.triage", **defaults):
            with patch("scout.triage.opik.Opik", return_value=client):
                return scout.load_system_prompt()

    @staticmethod
    def _chat_prompt(text):
        """A mock ChatPrompt storing the prompt as a single system message."""
        return MagicMock(template=[{"role": "system", "content": text}])

    @classmethod
    def _client(cls, *, get_return=None, get_side_effect=None):
        """A mock Opik client. get_chat_prompt returns a ChatPrompt (or None);
        create_chat_prompt records the messages it was asked to store."""
        c = MagicMock()
        if get_side_effect is not None:
            c.get_chat_prompt.side_effect = get_side_effect
        else:
            c.get_chat_prompt.return_value = get_return
        return c

    @staticmethod
    def _mismatch():
        return scout.PromptTemplateStructureMismatch(
            "scout-system-prompt", "text", "chat",
        )

    # --- existing Opik chat prompt is used verbatim ---------------------------

    def test_existing_opik_prompt_used_verbatim(self):
        # The system message content is returned untouched, and no new prompt
        # is created.
        client = self._client(
            get_return=self._chat_prompt("Triage for $repo_owner/$repo_name.")
        )
        result = self._call(client)
        assert result == "Triage for $repo_owner/$repo_name."
        client.create_chat_prompt.assert_not_called()

    def test_get_prompt_scoped_to_project_and_version(self):
        client = self._client(get_return=self._chat_prompt("x"))
        self._call(client, SCOUT_OPIK_PROMPT_VERSION="v3")
        client.get_chat_prompt.assert_called_once_with(
            name="scout-system-prompt",
            version="v3",
            project_name="scout:test-owner/test-repo",
        )

    # --- bootstrap: prompt missing -> create chat prompt from local base ------

    def test_creates_from_default_when_missing(self):
        client = self._client(get_return=None)
        result = self._call(client, REPO_OWNER="myorg", REPO_NAME="myrepo")
        # Created from the built-in default with placeholders fully resolved.
        assert "myorg/myrepo" in result
        assert "$repo_owner" not in result
        kwargs = client.create_chat_prompt.call_args.kwargs
        assert kwargs["name"] == "scout-system-prompt"
        assert kwargs["project_name"] == "scout:test-owner/test-repo"
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "myorg/myrepo" in messages[0]["content"]

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
        messages = client.create_chat_prompt.call_args.kwargs["messages"]
        assert messages == [{"role": "system", "content": "Custom prompt for acme."}]

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

    # --- migration: legacy text prompt -> chat prompt -------------------------

    def test_migrates_legacy_text_prompt_to_chat(self):
        # get_chat_prompt reports a structure mismatch; the existing text prompt
        # is copied, deleted (by prompt id, not version id), and recreated as a
        # chat prompt.
        client = self._client(get_side_effect=self._mismatch())
        client.rest_client.prompts.retrieve_prompt_version.return_value = MagicMock(
            template="Legacy text body.", prompt_id="prompt-123"
        )
        result = self._call(client)
        assert result == "Legacy text body."
        client.rest_client.prompts.delete_prompt.assert_called_once_with(id="prompt-123")
        kwargs = client.create_chat_prompt.call_args.kwargs
        assert kwargs["name"] == "scout-system-prompt"
        assert kwargs["project_name"] == "scout:test-owner/test-repo"
        assert kwargs["messages"] == [{"role": "system", "content": "Legacy text body."}]

    def test_migration_failure_falls_back_to_base(self):
        client = self._client(get_side_effect=self._mismatch())
        client.rest_client.prompts.retrieve_prompt_version.return_value = MagicMock(
            template="Legacy.", prompt_id="prompt-123"
        )
        client.rest_client.prompts.delete_prompt.side_effect = RuntimeError("boom")
        result = self._call(client, REPO_OWNER="myorg", REPO_NAME="myrepo")
        assert "myorg/myrepo" in result
        client.create_chat_prompt.assert_not_called()

    # --- resilience: Opik errors fall back to the local base prompt -----------

    def test_get_prompt_failure_falls_back_to_base(self):
        client = self._client(get_side_effect=RuntimeError("network down"))
        result = self._call(client, REPO_OWNER="myorg", REPO_NAME="myrepo")
        assert "myorg/myrepo" in result
        client.create_chat_prompt.assert_not_called()

    def test_create_failure_falls_back_to_base(self):
        client = self._client(get_return=None)
        client.create_chat_prompt.side_effect = RuntimeError("boom")
        result = self._call(client, REPO_OWNER="myorg", REPO_NAME="myrepo")
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
# GitHubProvider.ensure_label
# ---------------------------------------------------------------------------

class TestGitHubProviderEnsureLabel:
    def setup_method(self):
        self.provider = _make_github_provider()

    def test_returns_exists_without_creating(self):
        self.provider._repo.get_labels.return_value = [_label("bug"), _label("Escalated request")]
        result = self.provider.ensure_label("Escalated request")
        assert result == "exists"
        self.provider._repo.create_label.assert_not_called()

    def test_creates_when_missing(self):
        self.provider._repo.get_labels.return_value = [_label("bug")]
        result = self.provider.ensure_label("Escalated request")
        assert result == "created"
        self.provider._repo.create_label.assert_called_once_with("Escalated request", "e11d48")


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
# GitHubProvider.get_issue_data
# ---------------------------------------------------------------------------

class TestGitHubProviderGetIssueData:
    def setup_method(self):
        self.provider = _make_github_provider()

    @staticmethod
    def _comment(login: str, body: str, association: str) -> MagicMock:
        c = MagicMock()
        c.user.login = login
        c.body = body
        c.author_association = association
        return c

    def _issue(self, comments):
        issue = MagicMock()
        issue.number = 5
        issue.title = "t"
        issue.body = "b"
        issue.state = "open"
        issue.user.login = "alice"
        issue.author_association = "OWNER"
        issue.labels = [_label("bug")]
        issue.get_comments.return_value = comments
        self.provider._repo.get_issue.return_value = issue
        return issue

    def test_captures_issue_author_association(self):
        self._issue([])
        assert self.provider.get_issue_data(5)["author_association"] == "OWNER"

    def test_human_comment_role_and_association(self):
        self._issue([self._comment("carol", "me too", "CONTRIBUTOR")])
        comment = self.provider.get_issue_data(5)["comments"][0]
        assert comment["association"] == "CONTRIBUTOR"
        assert comment["role"] == "user"

    def test_scout_comment_detected_even_past_truncation(self):
        # The marker is appended at the end; a long Scout comment would have it
        # cut by the 500-char body truncation, so detection must use the full body.
        long_body = "Scout analysis. " * 100 + "<!-- scout-feedback trace_id=abc-123 -->"
        assert len(long_body) > 500
        self._issue([self._comment("scout[bot]", long_body, "NONE")])
        comment = self.provider.get_issue_data(5)["comments"][0]
        assert comment["role"] == "assistant"
        assert len(comment["body"]) <= 500

    def test_comment_body_truncated_to_500(self):
        self._issue([self._comment("alice", "x" * 600, "NONE")])
        assert len(self.provider.get_issue_data(5)["comments"][0]["body"]) == 500

    def test_keeps_most_recent_20_comments(self):
        # get_comments() is oldest-first; on a comment trigger the newest comments
        # (incl. the one that fired the run) must survive, so we keep the tail.
        self._issue([self._comment("u", f"c{i}", "NONE") for i in range(25)])
        bodies = [c["body"] for c in self.provider.get_issue_data(5)["comments"]]
        assert len(bodies) == 20
        assert bodies[0] == "c5"
        assert bodies[-1] == "c24"


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
# build_conversation  (agent.py)
# ---------------------------------------------------------------------------

class TestBuildConversation:
    def _issue(self, **overrides):
        base = {
            "number": 1,
            "title": "Something broke",
            "author": "user1",
            "author_association": "NONE",
            "labels": [],
            "state": "open",
            "body": "It does not work.",
            "comments": [],
        }
        base.update(overrides)
        return base

    def test_first_turn_is_user_with_title_and_body(self):
        messages, _ = agent.build_conversation(self._issue())
        assert messages[0]["role"] == "user"
        assert "Something broke" in messages[0]["content"]
        assert "It does not work." in messages[0]["content"]

    def test_reporter_labeled_with_association(self):
        messages, _ = agent.build_conversation(
            self._issue(author="alice", author_association="OWNER")
        )
        assert "[alice (OWNER)]" in messages[0]["content"]

    def test_human_comments_prefixed_with_author_and_association(self):
        issue = self._issue(comments=[
            {"author": "gituser23", "association": "CONTRIBUTOR", "body": "How do I do that?", "role": "user"},
        ])
        messages, _ = agent.build_conversation(issue)
        joined = "\n".join(m["content"] for m in messages)
        assert "[gituser23 (CONTRIBUTOR)]: How do I do that?" in joined

    def test_scout_comments_become_assistant_turns(self):
        issue = self._issue(comments=[
            {"author": "carol", "association": "MEMBER", "body": "Any update?", "role": "user"},
            {"author": "scout-bot", "association": "NONE", "body": "Looking into it.", "role": "assistant"},
            {"author": "carol", "association": "MEMBER", "body": "Thanks!", "role": "user"},
        ])
        messages, _ = agent.build_conversation(issue)
        roles = [m["role"] for m in messages]
        # The assistant turn appears between user turns; no role-prefix leaks in.
        assert "assistant" in roles
        assistant = next(m for m in messages if m["role"] == "assistant")
        assert assistant["content"] == "Looking into it."

    def test_roles_strictly_alternate(self):
        # Consecutive human comments (all 'user') must be merged so the Messages
        # API sees alternating roles.
        issue = self._issue(comments=[
            {"author": "a", "association": "NONE", "body": "one", "role": "user"},
            {"author": "b", "association": "NONE", "body": "two", "role": "user"},
            {"author": "scout-bot", "association": "NONE", "body": "reply", "role": "assistant"},
            {"author": "c", "association": "NONE", "body": "three", "role": "user"},
        ])
        messages, _ = agent.build_conversation(issue)
        roles = [m["role"] for m in messages]
        assert all(a != b for a, b in zip(roles, roles[1:])), roles
        assert roles[0] == "user"

    def test_merged_user_turn_preserves_each_speaker(self):
        issue = self._issue(comments=[
            {"author": "a", "association": "NONE", "body": "one", "role": "user"},
            {"author": "b", "association": "MEMBER", "body": "two", "role": "user"},
        ])
        messages, _ = agent.build_conversation(issue)
        joined = "\n".join(m["content"] for m in messages)
        assert "[a (NONE)]: one" in joined
        assert "[b (MEMBER)]: two" in joined

    def test_latest_turn_is_most_recent_human_comment(self):
        issue = self._issue(comments=[
            {"author": "a", "association": "NONE", "body": "first", "role": "user"},
            {"author": "scout-bot", "association": "NONE", "body": "reply", "role": "assistant"},
            {"author": "bob", "association": "MEMBER", "body": "the latest question", "role": "user"},
        ])
        _, latest = agent.build_conversation(issue)
        assert latest == "[bob (MEMBER)]: the latest question"

    def test_latest_turn_defaults_to_issue_body_when_no_comments(self):
        _, latest = agent.build_conversation(self._issue(author="alice", body="the bug"))
        assert "[alice (NONE)]: the bug" in latest

    def test_final_instruction_present(self):
        messages, _ = agent.build_conversation(self._issue())
        assert "most recent comment" in messages[-1]["content"]

    def test_labels_shown(self):
        messages, _ = agent.build_conversation(self._issue(labels=["bug", "help wanted"]))
        assert "bug" in messages[0]["content"]
        assert "help wanted" in messages[0]["content"]

    def test_no_labels_shows_none(self):
        messages, _ = agent.build_conversation(self._issue(labels=[]))
        assert "none" in messages[0]["content"]


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

    def test_add_comment_appends_with_association(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.add_comment(7, author="alice", body="hello", association="MEMBER")
        comment = sim.get_issue_data(7)["comments"][0]
        assert comment == {"author": "alice", "body": "hello", "association": "MEMBER", "role": "user"}

    def test_get_issue_data_normalizes_comment_role_and_association(self):
        sim = GitHubSimulator().add_issue(
            7, title="t", body="b",
            comments=[{"author": "alice", "body": "hi"}, {"author": "scout-bot", "body": "reply"}],
        )
        comments = sim.get_issue_data(7)["comments"]
        assert comments[0]["association"] == "NONE"
        assert comments[0]["role"] == "user"
        # scout-bot authored comments are treated as assistant turns.
        assert comments[1]["role"] == "assistant"

    def test_get_issue_data_includes_author_association(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b", author_association="OWNER")
        assert sim.get_issue_data(7)["author_association"] == "OWNER"

    def test_get_issue_data_defaults_author_association(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        assert sim.get_issue_data(7)["author_association"] == "NONE"

    def test_add_comment_explicit_role_overrides(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.add_comment(7, author="x", body="y", role="assistant")
        assert sim.get_issue_data(7)["comments"][0]["role"] == "assistant"

    def test_comment_body_truncated_to_500_like_real_provider(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.add_comment(7, author="alice", body="x" * 600)
        assert len(sim.get_issue_data(7)["comments"][0]["body"]) == 500

    def test_keeps_most_recent_20_comments_like_real_provider(self):
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        for i in range(25):
            sim.add_comment(7, author="u", body=f"c{i}")
        bodies = [c["body"] for c in sim.get_issue_data(7)["comments"]]
        assert len(bodies) == 20
        assert bodies[0] == "c5"
        assert bodies[-1] == "c24"

    def test_marker_role_detected_before_truncation(self):
        # Role is computed on the full body, so a long Scout comment whose marker
        # sits past char 500 is still detected as an assistant turn.
        sim = GitHubSimulator().add_issue(7, title="t", body="b")
        sim.add_comment(7, author="ext", body="reply " * 100 + "<!-- scout-feedback trace_id=z -->")
        comment = sim.get_issue_data(7)["comments"][0]
        assert comment["role"] == "assistant"
        assert len(comment["body"]) == 500


class TestGitHubSimulatorUpstream:
    """When constructed with an upstream provider, file-side reads delegate."""

    def _upstream(self) -> MagicMock:
        up = MagicMock()
        up.get_file_contents.return_value = "real-file-body"
        up.list_directory.return_value = ["src/", "README.md"]
        up.fetch_readme.return_value = "# real readme"
        return up

    def test_get_file_contents_delegates(self):
        up = self._upstream()
        sim = GitHubSimulator(upstream=up)
        assert sim.get_file_contents("any/path.py") == "real-file-body"
        up.get_file_contents.assert_called_once_with("any/path.py")

    def test_list_directory_delegates(self):
        up = self._upstream()
        sim = GitHubSimulator(upstream=up)
        assert sim.list_directory("src") == ["src/", "README.md"]
        up.list_directory.assert_called_once_with("src")

    def test_fetch_readme_delegates(self):
        up = self._upstream()
        sim = GitHubSimulator(upstream=up)
        assert sim.fetch_readme() == "# real readme"
        up.fetch_readme.assert_called_once_with()

    def test_upstream_shadows_local_files(self):
        # When upstream is set, local files (if any) are ignored — delegation
        # is total, not a fallback. Scenarios.py prevents mixing, but the
        # simulator's own behavior should be unambiguous.
        up = self._upstream()
        sim = GitHubSimulator(upstream=up).add_file("local.py", "local-body")
        assert sim.get_file_contents("local.py") == "real-file-body"

    def test_issue_reads_remain_simulated(self):
        up = self._upstream()
        sim = GitHubSimulator(upstream=up).add_issue(1, title="t", body="b")
        # Upstream is never consulted for issues.
        assert sim.get_issue_data(1)["title"] == "t"
        up.get_issue_data.assert_not_called()

    def test_writes_remain_simulated(self):
        up = self._upstream()
        sim = GitHubSimulator(upstream=up).add_issue(1, title="t", body="b")
        sim.apply_label(1, "bug")
        sim.post_comment(1, "hi")
        # Mutations land on the simulator; upstream is untouched.
        assert "bug" in sim.issue(1)["labels"]
        up.apply_label.assert_not_called()
        up.post_comment.assert_not_called()


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
        # "files": {} keeps the spec in simulated mode; we're only exercising
        # search behavior here, not file reads.
        spec = {
            "files": {},
            "issues": [{"number": 1, "title": "hang", "body": "deadlock"}],
        }
        sim = build("search-rate-limited", spec)
        # First two calls hit the underlying default search
        assert sim.search_issues("hang", 10) != []
        assert sim.search_issues("hang", 10) != []
        # Third+ return empty
        assert sim.search_issues("hang", 10) == []
        assert sim.search_issues("hang", 10) == []

    def test_default_builder_registered(self):
        assert "default" in SCENARIO_BUILDERS

    def test_real_mode_builds_upstream_when_files_absent(self, monkeypatch):
        # No "files" key → real-GitHub mode. _default constructs a
        # GitHubProvider with the spec's owner/name and the env token,
        # and passes it to the simulator as upstream.
        captured: dict = {}
        fake_upstream = MagicMock()

        def fake_provider_ctor(token, owner, name):
            captured["args"] = (token, owner, name)
            return fake_upstream

        monkeypatch.setenv("GITHUB_TOKEN", "ghp_real_token")
        monkeypatch.setattr("scout.providers.scenarios.GitHubProvider", fake_provider_ctor)

        spec = {"owner": "acme", "name": "widgets", "issues": [
            {"number": 1, "title": "t", "body": "b"},
        ]}
        sim = build("default", spec)

        assert captured["args"] == ("ghp_real_token", "acme", "widgets")
        # File-side reads now hit the upstream mock.
        fake_upstream.fetch_readme.return_value = "real readme"
        assert sim.fetch_readme() == "real readme"
        # Issues stayed simulated.
        assert sim.get_issue_data(1)["title"] == "t"

    def test_real_mode_raises_when_token_missing(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            build("default", {"owner": "acme", "name": "widgets"})

    def test_real_mode_raises_when_token_is_unused_sentinel(self, monkeypatch):
        # GITHUB_TOKEN=unused is the eval-time placeholder; real-mode scenarios
        # need an actual token.
        monkeypatch.setenv("GITHUB_TOKEN", "unused")
        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            build("default", {"owner": "acme", "name": "widgets"})

    def test_real_mode_rejects_readme_key(self, monkeypatch):
        # In real mode the README comes from GitHub — a stray "readme" key
        # would be silently shadowed, so we reject it loudly.
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_real_token")
        monkeypatch.setattr("scout.providers.scenarios.GitHubProvider", MagicMock())
        with pytest.raises(ValueError, match="readme"):
            build("default", {"owner": "acme", "name": "w", "readme": "x"})

    def test_simulated_mode_does_not_build_upstream(self, monkeypatch):
        # Even if GITHUB_TOKEN is set, presence of "files" keeps it offline.
        ctor = MagicMock()
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_real_token")
        monkeypatch.setattr("scout.providers.scenarios.GitHubProvider", ctor)
        build("default", {"files": {"a.py": "x"}, "issues": []})
        ctor.assert_not_called()


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
