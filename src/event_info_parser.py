import re
from dataclasses import dataclass
from datetime import date, time, datetime


@dataclass
class EventInfo:
    event_type: str
    event_date: date
    weekday: str
    start_time: time
    end_time: time


class EventInfoParser:
    _LINE_PATTERN = re.compile(
        r"^\s*(?P<event_type>.+?)\s+(?P<event_date>\d{4}-\d{2}-\d{2}),\s*"
        r"(?P<weekday>[A-Za-z]{3}),\s*(?P<time_range>.+?)\s*$"
    )
    _TIME_RANGE_SPLIT = re.compile(r"\s*-\s*")

    def parse_line(self, line: str) -> EventInfo:
        match = self._LINE_PATTERN.match(line)
        if not match:
            raise ValueError(f"Line does not match expected format: {line!r}")

        event_type = match.group("event_type").strip()
        event_date = datetime.strptime(match.group("event_date"), "%Y-%m-%d").date()
        weekday = match.group("weekday")

        time_range = match.group("time_range")
        time_parts = self._TIME_RANGE_SPLIT.split(time_range, maxsplit=1)
        if len(time_parts) != 2:
            raise ValueError(f"Missing '-' between start and end times: {line!r}")

        start_time = self._parse_time_lenient(time_parts[0])
        end_time = self._parse_time_lenient(time_parts[1])

        return EventInfo(event_type, event_date, weekday, start_time, end_time)

    def _parse_time_lenient(self, raw_time: str) -> time:
        """
        Accepts:
          "8" -> 08:00
          "08" -> 08:00
          "8:3" -> 08:03
          "8:30" -> 08:30
          "20:30" -> 20:30
          "20.30" -> 20:30
          "930" -> 09:30
          "2030" -> 20:30
        """
        normalized = raw_time.strip().replace(".", ":")

        # digits only: 3 or 4 -> HHmm / Hmm
        if re.fullmatch(r"\d{3,4}", normalized):
            if len(normalized) == 3:  # Hmm
                hours = "0" + normalized[0]
                minutes = normalized[1:]
            else:  # HHmm
                hours = normalized[:2]
                minutes = normalized[2:]
            return self._to_time(hours, minutes)

        # hour only
        if re.fullmatch(r"\d{1,2}", normalized):
            return self._to_time(self._pad_two_digits(normalized), "00")

        # H:MM, HH:MM, H:M, HH:M
        if re.fullmatch(r"\d{1,2}:\d{1,2}", normalized):
            hours, minutes = normalized.split(":")
            return self._to_time(self._pad_two_digits(hours), self._pad_two_digits(minutes))

        raise ValueError(f"Invalid time format: {raw_time!r}")

    def _pad_two_digits(self, value: str) -> str:
        return value if len(value) == 2 else "0" + value

    def _to_time(self, hours: str, minutes: str) -> time:
        hour_int = int(hours)
        minute_int = int(minutes)
        if not (0 <= hour_int <= 23):
            raise ValueError(f"Hour out of range: {hour_int}")
        if not (0 <= minute_int <= 59):
            raise ValueError(f"Minute out of range: {minute_int}")
        return time(hour=hour_int, minute=minute_int)


# --- Demo ---
if __name__ == "__main__":
    parser = EventInfoParser()
    lines = [
        "Game 2025-09-30, Tue, 20:00-22:00",
        "Training 2025-10-01, Wed, 20.30-22.00",
        "Match 2025-10-02, Thu, 930-1130",
        "Review 2025-10-03, Fri, 8-9",
    ]
    for line in lines:
        try:
            event = parser.parse_line(line)
            print(event)
        except Exception as exc:
            print(f"Failed: {line!r} -> {exc}")
