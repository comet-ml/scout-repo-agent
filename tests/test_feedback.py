"""Unit tests for scout.feedback."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scout import feedback as scout_feedback
from scout import triage as scout


def _reaction(content: str, login: str | None = "someone"):
    user = SimpleNamespace(login=login) if login is not None else None
    return SimpleNamespace(content=content, user=user)


# ---------------------------------------------------------------------------
# parse_trace_id
# ---------------------------------------------------------------------------

class TestParseTraceId:
    def test_extracts_trace_id(self):
        body = "Triage text.\n\n<!-- scout-feedback trace_id=abc-123-def -->"
        assert scout_feedback.parse_trace_id(body) == "abc-123-def"

    def test_returns_none_without_marker(self):
        assert scout_feedback.parse_trace_id("Just a normal comment.") is None

    def test_returns_none_for_empty(self):
        assert scout_feedback.parse_trace_id("") is None

    def test_ignores_marker_inside_other_text(self):
        body = "intro\n<!--scout-feedback trace_id=9f8e7d-6c5b-->\noutro"
        assert scout_feedback.parse_trace_id(body) == "9f8e7d-6c5b"

    def test_round_trips_with_scout_marker(self):
        # Guard against the producer (scout) and consumer (scout_feedback) drifting.
        trace_id = "0123abcd-4567-89ef-0123-456789abcdef"
        marker = scout._feedback_marker(trace_id)
        assert scout_feedback.parse_trace_id(marker) == trace_id


# ---------------------------------------------------------------------------
# collect_thumbs
# ---------------------------------------------------------------------------

class TestCollectThumbs:
    def test_collects_up_and_down_logins(self):
        reactions = [_reaction("+1", "alice"), _reaction("+1", "bob"), _reaction("-1", "carol")]
        assert scout_feedback.collect_thumbs(reactions) == (["alice", "bob"], ["carol"])

    def test_ignores_non_thumb_reactions(self):
        reactions = [_reaction("+1", "alice"), _reaction("heart", "x"),
                     _reaction("rocket", "y"), _reaction("-1", "carol")]
        assert scout_feedback.collect_thumbs(reactions) == (["alice"], ["carol"])

    def test_missing_user_recorded_as_unknown(self):
        reactions = [_reaction("+1", None)]
        assert scout_feedback.collect_thumbs(reactions) == (["unknown"], [])

    def test_empty(self):
        assert scout_feedback.collect_thumbs([]) == ([], [])


# ---------------------------------------------------------------------------
# format_reason
# ---------------------------------------------------------------------------

class TestFormatReason:
    def test_lists_logins_and_counts(self):
        reason = scout_feedback.format_reason(["alice", "bob"], ["carol"])
        assert reason == "👍 2 (alice, bob) / 👎 1 (carol) from GitHub"

    def test_empty_side_shown_as_dash(self):
        reason = scout_feedback.format_reason(["alice"], [])
        assert reason == "👍 1 (alice) / 👎 0 (—) from GitHub"


# ---------------------------------------------------------------------------
# main — the logged score must be scoped to the trace's project
# ---------------------------------------------------------------------------

class TestMainScoresProjectScoped:
    def test_score_includes_project_name(self, monkeypatch):
        monkeypatch.setenv("SCOUT_GITHUB_REPO_OWNER", "acme")
        monkeypatch.setenv("SCOUT_GITHUB_REPO_NAME", "widgets")
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("OPIK_API_KEY", "key")
        monkeypatch.setenv("OPIK_WORKSPACE", "ws")

        comment = SimpleNamespace(
            body="triage\n\n<!-- scout-feedback trace_id=0123abcd-4567-89ef -->",
            get_reactions=lambda: [_reaction("+1", "alice"), _reaction("+1", "bob"), _reaction("-1", "carol")],
        )
        issue = SimpleNamespace(number=5, get_comments=lambda: [comment])
        repo = MagicMock()
        repo.get_issues.return_value = [issue]
        gh = MagicMock()
        gh.get_repo.return_value = repo
        opik_client = MagicMock()

        with patch("scout.feedback.Github", return_value=gh), \
             patch("scout.feedback.opik.Opik", return_value=opik_client):
            scout_feedback.main()

        opik_client.log_traces_feedback_scores.assert_called_once()
        (scores,), _ = opik_client.log_traces_feedback_scores.call_args
        score = scores[0]
        # Without project_name the score would land in the client's default project,
        # not where the trace lives — see scout_feedback.main.
        assert score["project_name"] == "scout:acme/widgets"
        assert score["id"] == "0123abcd-4567-89ef"
        assert score["name"] == scout_feedback.FEEDBACK_SCORE_NAME
        assert score["value"] == 2 / 3


# ---------------------------------------------------------------------------
# compute_feedback
# ---------------------------------------------------------------------------

class TestComputeFeedback:
    def test_all_up_is_one(self):
        assert scout_feedback.compute_feedback(3, 0) == 1.0

    def test_all_down_is_zero(self):
        assert scout_feedback.compute_feedback(0, 2) == 0.0

    def test_mixed_is_ratio(self):
        assert scout_feedback.compute_feedback(3, 1) == 0.75

    def test_no_votes_is_none(self):
        assert scout_feedback.compute_feedback(0, 0) is None
