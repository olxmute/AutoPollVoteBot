import os
import unittest.mock

import pytest

from src.config import CommonConfig, ManagerBotConfig, load_config_from_template


# Minimal env vars required to render config.yaml.j2 successfully.
_BASE_ENV = {
    "PYROGRAM_API_ID": "12345",
    "PYROGRAM_API_HASH": "abc123",
    "GROUP_CHAT_ID": "-100987654321",
    "BOT_TOKEN": "111:AAA",
}

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml.j2")


class TestConfigWithBotToken:
    def test_renders_manager_bot_token(self):
        """Rendering with BOT_TOKEN set produces a valid CommonConfig with manager.bot_token."""
        with unittest.mock.patch.dict(os.environ, _BASE_ENV, clear=True):
            cfg = load_config_from_template(_TEMPLATE_PATH)

        assert isinstance(cfg, CommonConfig)
        assert isinstance(cfg.manager, ManagerBotConfig)
        assert cfg.manager.bot_token == "111:AAA"

    def test_notification_field_is_gone(self):
        """CommonConfig must not have a 'notification' attribute anymore."""
        with unittest.mock.patch.dict(os.environ, _BASE_ENV, clear=True):
            cfg = load_config_from_template(_TEMPLATE_PATH)

        assert not hasattr(cfg, "notification"), (
            "'notification' field still exists on CommonConfig; rename to 'manager'"
        )

    def test_manager_field_is_required(self):
        """CommonConfig.manager is a plain required field (no Optional / default)."""
        import inspect
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(CommonConfig)}
        assert "manager" in fields, "CommonConfig must have a 'manager' field"
        field = fields["manager"]
        # A required field has no default and no default_factory.
        has_default = field.default is not dataclasses.MISSING
        has_factory = field.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        assert not has_default and not has_factory, (
            "CommonConfig.manager must be required (no default value)"
        )


class TestConfigWithoutBotToken:
    def test_raises_when_bot_token_missing(self):
        """Rendering without BOT_TOKEN raises because of Jinja2 StrictUndefined."""
        env_without_token = {k: v for k, v in _BASE_ENV.items() if k != "BOT_TOKEN"}
        with unittest.mock.patch.dict(os.environ, env_without_token, clear=True):
            with pytest.raises((RuntimeError, ValueError)):
                load_config_from_template(_TEMPLATE_PATH)
