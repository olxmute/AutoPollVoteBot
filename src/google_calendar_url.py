"""Pure URL builder for the "Add to Google Calendar" inline button.

Builds a Google Calendar render-URL that prefills the new-event sheet with
the event title and start/end times in UTC. No OAuth, no API calls — purely
a query-string construction.
"""

from urllib.parse import quote

from src.event_info_parser import EventInfo
from src.reminder_discovery import prague_datetime_to_utc

_BASE = "https://calendar.google.com/calendar/render?action=TEMPLATE"
_UTC_FORMAT = "%Y%m%dT%H%M%SZ"


def build_add_event_url(event_info: EventInfo) -> str:
    """Build a Google Calendar add-event URL for the given event.

    The title is hardcoded as ``f"Badminton {event_info.event_type}"`` and
    URL-encoded via :func:`urllib.parse.quote`. Start/end times are
    converted from Prague local time to UTC and formatted as
    ``YYYYMMDDTHHMMSSZ``.
    """
    title = quote(f"Badminton {event_info.event_type}")
    start = prague_datetime_to_utc(event_info.event_date, event_info.start_time).strftime(_UTC_FORMAT)
    end = prague_datetime_to_utc(event_info.event_date, event_info.end_time).strftime(_UTC_FORMAT)
    return f"{_BASE}&text={title}&dates={start}/{end}"
