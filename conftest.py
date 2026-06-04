"""Pytest configuration: stub heavy dependencies and set required env vars before scout is imported."""
import os
import sys
from unittest.mock import MagicMock

# Required env vars consumed at import time
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("SCOUT_GITHUB_REPO_OWNER", "test-owner")
os.environ.setdefault("SCOUT_GITHUB_REPO_NAME", "test-repo")
os.environ.setdefault("ISSUE_NUMBER", "42")
# Opik is a hard requirement at import time.
os.environ.setdefault("OPIK_API_KEY", "test-opik-key")
os.environ.setdefault("OPIK_WORKSPACE", "test-workspace")

# Stub out packages that make network calls or require real credentials. Force the
# opik stubs (assignment, not setdefault) because the real opik SDK may already be
# importable on the path — scout now configures Opik at import time, so the real
# client would try to validate the fake test credentials.
_opik_mock = MagicMock()
_opik_mock.configure = MagicMock()
_opik_mock.track = lambda *a, **kw: (lambda fn: fn)  # pass-through decorator
sys.modules["opik"] = _opik_mock
sys.modules["opik.opik_context"] = MagicMock()

_opik_anthropic_mock = MagicMock()
_opik_anthropic_mock.track_anthropic = lambda client, **kw: client
sys.modules["opik.integrations.anthropic"] = _opik_anthropic_mock

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("dotenv", MagicMock())
