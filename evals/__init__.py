"""Scout evaluation harness: scenarios and seed script for the Opik Test Suite."""
from __future__ import annotations

import contextlib
import os
from typing import Iterator


@contextlib.contextmanager
def unpinned_prompt_environment() -> Iterator[None]:
    """Resolve the system prompt by latest version instead of by environment.

    OPIK_ENVIRONMENT does double duty: the Opik SDK stamps it on every trace and
    span, and triage.load_system_prompt() uses it to pick a prompt version.
    Environment-driven resolution is a hard error when no prompt version is
    linked to that environment, which is currently the case for every
    environment — so the eval runners would fail before reaching the agent.

    Clearing the variable for the duration of the prompt fetch unpins the lookup
    (load_system_prompt falls back to "latest") while leaving trace tagging
    intact, since the variable is restored immediately afterwards. Drop this once
    prompt versions are linked to environments in the Opik UI.
    """
    saved = os.environ.pop("OPIK_ENVIRONMENT", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["OPIK_ENVIRONMENT"] = saved
