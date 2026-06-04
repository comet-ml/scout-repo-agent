"""Unit tests for init_scout.py."""
from unittest.mock import MagicMock

from opik.exceptions import PromptTemplateStructureMismatch

import init_scout
import scout


def _mismatch() -> PromptTemplateStructureMismatch:
    return PromptTemplateStructureMismatch(scout.SCOUT_OPIK_PROMPT_NAME, "text", "chat")


class TestEnsurePrompt:
    def test_exists_is_not_recreated(self):
        client = MagicMock()
        client.get_chat_prompt.return_value = MagicMock()  # truthy ChatPrompt
        assert init_scout.ensure_prompt(client) == "exists"
        client.create_chat_prompt.assert_not_called()

    def test_created_when_missing(self):
        client = MagicMock()
        client.get_chat_prompt.return_value = None
        assert init_scout.ensure_prompt(client) == "created"
        kwargs = client.create_chat_prompt.call_args.kwargs
        assert kwargs["name"] == scout.SCOUT_OPIK_PROMPT_NAME
        assert kwargs["project_name"] == scout.OPIK_PROJECT
        # Seeded from the base prompt as a single system message.
        assert kwargs["messages"][0]["role"] == "system"
        assert kwargs["messages"][0]["content"]

    def test_migrates_legacy_text_prompt(self):
        client = MagicMock()
        client.get_chat_prompt.side_effect = _mismatch()
        client.rest_client.prompts.retrieve_prompt_version.return_value = MagicMock(
            template="Legacy body.", prompt_id="prompt-123"
        )
        assert init_scout.ensure_prompt(client) == "migrated"
        # Reuses scout's migration: delete by prompt id, recreate as chat.
        client.rest_client.prompts.delete_prompt.assert_called_once_with(id="prompt-123")
        kwargs = client.create_chat_prompt.call_args.kwargs
        assert kwargs["messages"] == [{"role": "system", "content": "Legacy body."}]


class TestEnsureEscalationLabel:
    def test_delegates_to_provider(self):
        provider = MagicMock()
        provider.ensure_label.return_value = "created"
        assert init_scout.ensure_escalation_label(provider) == "created"
        provider.ensure_label.assert_called_once_with(scout.SCOUT_ESCALATION_TAG)
