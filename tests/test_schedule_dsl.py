"""Tests for schedule_dsl module — parse_schedule_dsl and serialize_schedule_dsl."""

import pytest
from src.schedule_dsl import parse_schedule_dsl, serialize_schedule_dsl


class TestParseScheduleDsl:
    def test_parse_rejects_three_tokens(self):
        """Parser must reject entries with three tokens (e.g. 'Game wed 20:30')."""
        with pytest.raises(ValueError):
            parse_schedule_dsl("Game wed 20:30")

    def test_parse_rejects_one_token(self):
        """Parser must reject entries with only one token (e.g. 'Game')."""
        with pytest.raises(ValueError):
            parse_schedule_dsl("Game")


class TestSerializeScheduleDsl:
    def test_roundtrip_parse_serialize_parse(self):
        """Roundtrip: parse → serialize → parse yields identical results and original string."""
        dsl = "Game wed; Training tue"
        parsed_once = parse_schedule_dsl(dsl)
        serialized = serialize_schedule_dsl(parsed_once)
        parsed_twice = parse_schedule_dsl(serialized)
        assert parsed_once == parsed_twice
        assert serialized == dsl

    def test_empty_list_serializes_to_empty_string(self):
        """Empty list → empty string."""
        assert serialize_schedule_dsl([]) == ""

    def test_empty_string_roundtrip(self):
        """Empty DSL string → parse → serialize → empty list → empty string."""
        parsed = parse_schedule_dsl("")
        assert parsed == []
        serialized = serialize_schedule_dsl(parsed)
        assert serialized == ""

    def test_serialize_single_entry_no_separator(self):
        """Single entry should not have a trailing or leading separator."""
        events = [{"type": "Game", "day": "wed"}]
        result = serialize_schedule_dsl(events)
        assert result == "Game wed"
        assert ";" not in result

    def test_serialize_multiple_entries_uses_semicolon_separator(self):
        """Multiple entries joined by '; '."""
        events = [
            {"type": "Game", "day": "wed"},
            {"type": "Training", "day": "fri"},
        ]
        result = serialize_schedule_dsl(events)
        assert result == "Game wed; Training fri"

    def test_serialize_ignores_extra_keys(self):
        """serialize_schedule_dsl only uses 'type' and 'day'; extra keys are silently ignored."""
        events = [{"type": "Game", "day": "wed", "start_time": "20:30"}]
        result = serialize_schedule_dsl(events)
        assert result == "Game wed"
