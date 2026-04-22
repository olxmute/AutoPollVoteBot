from dataclasses import dataclass

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


@dataclass
class DatabaseConfig:
    path: str


@dataclass
class ServerConfig:
    port: int


@dataclass
class ManagerBotConfig:
    bot_token: str


@dataclass
class CommonConfig(YAMLWizard):
    pyrogram: PyrogramConfig
    group: GroupConfig
    database: DatabaseConfig
    server: ServerConfig
    manager: ManagerBotConfig


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
