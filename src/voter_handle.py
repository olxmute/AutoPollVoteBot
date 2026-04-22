"""VoterHandle dataclass — shared between AutoPollManagerBot and ScheduleEditor.

Kept in its own module to avoid a circular import between auto_poll_manager_bot
(which imports ScheduleEditor) and schedule_editor (which needs VoterHandle).
"""
from dataclasses import dataclass

from pyrogram import Client

from src.user_repository import UserRecord


@dataclass
class VoterHandle:
    """Thin wrapper associating a voter's UserRecord with its Pyrogram Client.

    The ``enabled`` state lives on ``user.enabled`` — the same UserRecord
    instance is shared by reference with the voter, so mutations here are
    immediately visible in the voter's ``on_forum_message`` guard.
    """

    user: UserRecord
    client: Client
