from dataclasses import dataclass
from datetime import time
from typing import List, Optional

from dataclass_wizard import YAMLWizard

from src.yaml_renderer import render_yaml_template


@dataclass
class PyrogramConfig:
    api_id: int
    api_hash: str
    session_name: str = "user"


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
class EventConfig:
    schedule: List[ScheduledEvent]


@dataclass
class AppConfig(YAMLWizard):
    pyrogram: PyrogramConfig
    group: GroupConfig
    event: EventConfig


def load_config_from_template(template_path: str) -> AppConfig:
    """
    Render the Jinja2 YAML template, then let dataclass-wizard parse it
    directly via `from_yaml`.
    """
    rendered_yaml = render_yaml_template(template_path)
    try:
        cfg = AppConfig.from_yaml(rendered_yaml)
    except Exception as e:
        # Surface a helpful message if parsing/types fail
        raise ValueError(
            f"Failed to build AppConfig from rendered YAML: {e}\nRendered YAML:\n{rendered_yaml}"
        ) from e
    return cfg
