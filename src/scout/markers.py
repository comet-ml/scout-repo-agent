"""Shared marker constant for Scout's hidden comment signature.

Scout stamps a hidden HTML marker into every comment it posts. The full marker
(written by scout.triage._feedback_marker) is::

    <!-- scout-feedback trace_id=<uuid> -->

Two consumers key off it:
  - scout.feedback maps a comment's 👍/👎 reactions back to its Opik trace
    (it parses the trace id with scout.feedback.MARKER_RE), and
  - the triage agent recognizes its own past comments in a thread so it can
    render them as assistant turns and skip re-triaging when one triggers a run.

SCOUT_COMMENT_MARKER is the trace-id-independent prefix; a substring test against
it is enough to tell "this comment was written by Scout". Keep it in sync with
_feedback_marker and scout.feedback.MARKER_RE.
"""
from __future__ import annotations

SCOUT_COMMENT_MARKER = "<!-- scout-feedback"
