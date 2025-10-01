from typing import List


def parse_schedule_dsl(dsl_string: str) -> List[dict]:
    """
    Parse a schedule DSL string into a list of event dictionaries.

    Format: "Type day [HH:MM]; Type day [HH:MM]; ..."
    Example: "Game wed 20:30; Game sat 11:00; Game sun"

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
        if len(parts) < 2:
            raise ValueError(f"Invalid schedule entry: '{entry}'. Expected format: 'Type day [time]'")

        event_type = parts[0]
        day = parts[1]
        start_time = parts[2] if len(parts) >= 3 else None

        event = {
            'type': event_type,
            'day': day,
        }
        if start_time:
            event['start_time'] = start_time

        events.append(event)

    return events
