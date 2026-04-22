from typing import List


def serialize_schedule_dsl(events: List[dict]) -> str:
    """
    Inverse of parse_schedule_dsl. Converts a list of event dicts to a DSL string.

    Input dicts must match parse output shape:
    {'type': str, 'day': str, 'start_time': str (optional)}.
    start_time is a string like 'HH:MM' — NOT a datetime.time. Callers that hold
    ScheduledEvent instances must not asdict() them here (the time coercion in
    __post_init__ would produce 'HH:MM:SS' and break the roundtrip).

    Returns empty string for empty input.
    """
    parts = []
    for e in events:
        entry = f"{e['type']} {e['day']}"
        if e.get('start_time'):
            entry += f" {e['start_time']}"
        parts.append(entry)
    return "; ".join(parts)


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
