from typing import List


def serialize_schedule_dsl(events: List[dict]) -> str:
    """
    Inverse of parse_schedule_dsl. Converts a list of event dicts to a DSL string.

    Input dicts must match parse output shape: {'type': str, 'day': str}.

    Returns empty string for empty input.
    """
    parts = []
    for e in events:
        entry = f"{e['type']} {e['day']}"
        parts.append(entry)
    return "; ".join(parts)


def parse_schedule_dsl(dsl_string: str) -> List[dict]:
    """
    Parse a schedule DSL string into a list of event dictionaries.

    Format: "Type day; Type day; ..."
    Example: "Game wed; Game sat; Game sun"

    Returns a list of dicts suitable for YAML serialization.
    """
    if not dsl_string or not dsl_string.strip():
        return []

    events = []
    entries = dsl_string.split(';')

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        parts = entry.split()
        if len(parts) != 2:
            raise ValueError(f"Invalid schedule entry: '{entry}'. Expected format: 'Type day'")

        event_type = parts[0]
        day = parts[1]
        event = {
            'type': event_type,
            'day': day,
        }
        events.append(event)

    return events
