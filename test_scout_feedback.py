"""Unit tests for scout_feedback.py."""
from types import SimpleNamespace

import scout
import scout_feedback


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
