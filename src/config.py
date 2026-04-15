from dataclasses import dataclass
from datetime import time
from typing import Optional

from dataclass_wizard import YAMLWizard

from src.yaml_renderer import render_yaml_template


@dataclass
class PyrogramConfig:
    api_id: int
    api_hash: str


@dataclass
class GroupConfig:
    chat_id: int
    vote_option: str


@dataclass
class ScheduledEvent:
    type: str
    day: str
    start_time: Optional[time] = None  # Parsed from "HH:MM" string

    def __post_init__(self):
        # Convert string to time if needed (for flexibility)
        if isinstance(self.start_time, str):
            self.start_time = time.fromisoformat(self.start_time)


@dataclass
class DatabaseConfig:
    path: str


@dataclass
class ServerConfig:
    port: int
    ping_url: str
    enable_self_ping: bool


@dataclass
class NotificationConfig:
    bot_token: str


@dataclass
class CommonConfig(YAMLWizard):
    pyrogram: PyrogramConfig
    group: GroupConfig
    database: DatabaseConfig
    server: ServerConfig
    notification: Optional[NotificationConfig] = None


def load_config_from_template(template_path: str) -> CommonConfig:
    """
    Render the Jinja2 YAML template, then let dataclass-wizard parse it
    directly via `from_yaml`.
    """
    rendered_yaml = render_yaml_template(template_path)
    try:
        cfg = CommonConfig.from_yaml(rendered_yaml)
    except Exception as e:
        # Surface a helpful message if parsing/types fail
        raise ValueError(
            f"Failed to build CommonConfig from rendered YAML: {e}\nRendered YAML:\n{rendered_yaml}"
        ) from e
    return cfg
