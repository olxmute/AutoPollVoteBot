"""Tests for build_add_event_url (pure URL builder).

Covers:
  - happy path: known EventInfo -> exact expected URL
  - Training event: confirms `Badminton ` prefix concatenation
  - DST boundary: post-CEST switch yields a different UTC offset than summer
  - percent-encoding: `&` and spaces in event_type don't break the query string
"""
from datetime import date, time
from urllib.parse import parse_qs, urlparse

from src.event_info_parser import EventInfo
from src.google_calendar_url import build_add_event_url


def _make_event_info(
    event_type: str = "Game",
    event_date: date = date(2026, 5, 1),
    weekday: str = "Fri",
    start_time: time = time(20, 0),
    end_time: time = time(22, 0),
) -> EventInfo:
    return EventInfo(
        event_type=event_type,
        event_date=event_date,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
    )


class TestBuildAddEventUrlHappyPath:
    def test_summer_time_game_event_yields_exact_url(self):
        # 2026-05-01 is in CEST (UTC+2): 20:00 Prague -> 18:00 UTC, 22:00 -> 20:00.
        event_info = _make_event_info(
            event_type="Game",
            event_date=date(2026, 5, 1),
            weekday="Fri",
            start_time=time(20, 0),
            end_time=time(22, 0),
        )
        url = build_add_event_url(event_info)

        # Assert scheme/host/path are correct.
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "calendar.google.com"
        assert parsed.path == "/calendar/render"

        # Assert query params via parse_qs (order-independent — robust if the
        # builder ever switches to urllib.parse.urlencode).
        params = parse_qs(parsed.query)
        assert params["action"] == ["TEMPLATE"]
        assert params["text"] == ["Badminton Game"]
        assert params["dates"] == ["20260501T180000Z/20260501T200000Z"]


class TestBuildAddEventUrlPrefix:
    def test_training_event_uses_badminton_prefix(self):
        event_info = _make_event_info(event_type="Training")
        url = build_add_event_url(event_info)
        assert "text=Badminton%20Training" in url


class TestBuildAddEventUrlDst:
    def test_post_cest_switch_uses_utc_plus_one(self):
        # 2026-11-04 is after Prague's CEST->CET switch (last Sunday of October
        # 2026 = 2026-10-25). At that point Prague is UTC+1, so 20:00 -> 19:00 UTC
        # (distinct from the summer-time test which yielded 18:00 UTC).
        event_info = _make_event_info(
            event_type="Game",
            event_date=date(2026, 11, 4),
            weekday="Wed",
            start_time=time(20, 0),
            end_time=time(22, 0),
        )
        url = build_add_event_url(event_info)
        assert "dates=20261104T190000Z/20261104T210000Z" in url


class TestBuildAddEventUrlEncoding:
    def test_event_type_with_space_and_ampersand_is_percent_encoded(self):
        event_info = _make_event_info(event_type="Game & Drills")
        url = build_add_event_url(event_info)

        # The encoded form must appear in the raw URL string.
        assert "Badminton%20Game%20%26%20Drills" in url

        # Round-trip: parse the URL and confirm `text` decodes back to the
        # original title with the literal `&`.
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert params["text"] == ["Badminton Game & Drills"]

        # Slice the `text=...` segment from the raw URL string and confirm
        # there are no unescaped `&` chars inside it (they would otherwise be
        # interpreted as a query-parameter delimiter).
        text_start = url.index("text=") + len("text=")
        text_end = url.index("&dates=")
        text_segment = url[text_start:text_end]
        assert "&" not in text_segment

    def test_event_type_with_slash_and_plus_round_trips(self):
        """`/` and `+` in event_type must round-trip cleanly through parse_qs.

        `/` is not escaped by `urllib.parse.quote` with default `safe='/'`, but
        it is a valid query-value char per RFC 3986 (no conflict with `&`/`=`),
        so `parse_qs` decodes it back unchanged. `+` IS escaped to `%2B`,
        which guarantees `parse_qs` doesn't decode it as a space.
        """
        event_info = _make_event_info(event_type="A/B+C")
        url = build_add_event_url(event_info)

        # `+` must be encoded as `%2B` (otherwise parse_qs would decode it as space).
        assert "%2B" in url

        # Round-trip via parse_qs.
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert params["text"] == ["Badminton A/B+C"]
