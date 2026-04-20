"""Tests for schedule_dsl module — parse_schedule_dsl and serialize_schedule_dsl."""

import pytest
from src.schedule_dsl import parse_schedule_dsl, serialize_schedule_dsl


class TestSerializeScheduleDsl:
    def test_byte_exact_roundtrip_with_start_time(self):
        """Proves no 'HH:MM:SS' drift — time must stay as 'HH:MM'."""
        dsl = "Game wed 20:30"
        result = serialize_schedule_dsl(parse_schedule_dsl(dsl))
        assert result == dsl

    def test_roundtrip_parse_serialize_parse_with_start_times(self):
        """parse → serialize → parse for entries with start_time."""
        dsl = "Game wed 20:30; Training tue 18:00"
        parsed_once = parse_schedule_dsl(dsl)
        serialized = serialize_schedule_dsl(parsed_once)
        parsed_twice = parse_schedule_dsl(serialized)
        assert parsed_once == parsed_twice

    def test_roundtrip_without_start_time(self):
        """Roundtrip for entries without start_time."""
        dsl = "Game wed; Training tue"
        parsed_once = parse_schedule_dsl(dsl)
        serialized = serialize_schedule_dsl(parsed_once)
        parsed_twice = parse_schedule_dsl(serialized)
        assert parsed_once == parsed_twice
        assert serialized == dsl

    def test_roundtrip_mixed_with_and_without_start_time(self):
        """Roundtrip for mixed entries (some with, some without start_time)."""
        dsl = "Game wed 20:30; Training tue"
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

    def test_serialize_preserves_start_time_in_existing_entries(self):
        """Existing time-bearing DB entries must not lose their start_time."""
        events = [
            {"type": "Game", "day": "wed", "start_time": "20:30"},
            {"type": "Training", "day": "tue"},
        ]
        result = serialize_schedule_dsl(events)
        assert "20:30" in result
        assert "Game wed 20:30" in result
        assert "Training tue" in result

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

    def test_start_time_none_omitted(self):
        """start_time=None should be omitted from output."""
        events = [{"type": "Game", "day": "wed", "start_time": None}]
        result = serialize_schedule_dsl(events)
        assert result == "Game wed"

    def test_byte_exact_roundtrip_multiple_entries(self):
        """Byte-exact roundtrip for multiple entries."""
        dsl = "Game wed 20:30; Training tue 18:00"
        result = serialize_schedule_dsl(parse_schedule_dsl(dsl))
        assert result == dsl
