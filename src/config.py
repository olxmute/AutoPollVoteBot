from dataclasses import dataclass
from datetime import time
from typing import List, Optional

from dataclass_wizard import YAMLWizard


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


def load_config(path: str = "config.yaml") -> AppConfig:
    return AppConfig.from_yaml_file(path)
