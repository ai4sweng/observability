"""Unit tests for instant parsing, output formatting, and step alignment.

Alignment is the part with real consequences: an unaligned daily bucket looks
like a calendar day but is not one, so two reports run an hour apart disagree
about the same "day" without either being obviously wrong.
"""
from datetime import datetime, timezone

import pytest

from metrics_api import timefmt


class TestIso8601:
    def test_formats_as_utc_with_a_z_suffix(self):
        assert timefmt.iso8601(1_757_000_000) == "2025-09-04T15:33:20Z"

    def test_drops_sub_second_precision(self):
        # VictoriaMetrics returns fractional seconds; they add noise to a
        # human-facing timestamp and nothing to the value.
        assert timefmt.iso8601(1_757_000_000.489) == "2025-09-04T15:33:20Z"

    def test_midnight_round_trips(self):
        unix = datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp()
        assert timefmt.iso8601(unix) == "2026-09-09T00:00:00Z"


class TestParseInstant:
    @pytest.mark.parametrize(
        "value",
        ["2026-09-08T00:00:00Z", "2026-09-08T00:00:00+00:00", "2026-09-08T00:00:00"],
    )
    def test_iso_forms_all_parse_to_the_same_instant(self, value):
        # A bare timestamp with no offset is read as UTC, matching how
        # VictoriaMetrics treats one.
        expected = datetime(2026, 9, 8, tzinfo=timezone.utc).timestamp()
        assert timefmt.parse_instant(value, "start") == expected

    def test_offset_is_respected(self):
        utc = timefmt.parse_instant("2026-09-08T03:00:00+03:00", "start")
        assert utc == datetime(2026, 9, 8, tzinfo=timezone.utc).timestamp()

    def test_unix_seconds_parse(self):
        assert timefmt.parse_instant("1757000000", "start") == 1_757_000_000.0

    @pytest.mark.parametrize("bad", ["", "  ", "yesterday", "2026-13-45", "not-a-time"])
    def test_unparseable_is_refused(self, bad):
        with pytest.raises(timefmt.InvalidTime) as excinfo:
            timefmt.parse_instant(bad, "start")
        assert "start" in str(excinfo.value)


class TestZone:
    def test_utc_needs_no_tz_database(self):
        # Neither Windows nor python:*-slim ships /usr/share/zoneinfo, so the
        # default path must not depend on it.
        assert timefmt.zone("UTC") is timezone.utc
        assert timefmt.zone("utc") is timezone.utc

    def test_named_zone_resolves(self):
        assert timefmt.zone("Europe/Istanbul") is not None

    def test_unknown_zone_is_refused_with_guidance(self):
        with pytest.raises(timefmt.InvalidTime) as excinfo:
            timefmt.zone("Mars/Olympus_Mons")
        assert "IANA" in str(excinfo.value)


class TestShouldAlign:
    @pytest.mark.parametrize("step_seconds", [86400, 3600, 7200])
    def test_auto_aligns_hourly_and_daily_steps(self, step_seconds):
        assert timefmt.should_align(step_seconds, "auto") is True

    @pytest.mark.parametrize("step_seconds", [15, 36, 300, 864])
    def test_auto_leaves_sub_hourly_steps_alone(self, step_seconds):
        # A 36s bucket has no calendar boundary worth snapping to.
        assert timefmt.should_align(step_seconds, "auto") is False

    def test_calendar_always_aligns(self):
        assert timefmt.should_align(15, "calendar") is True

    def test_none_never_aligns(self):
        assert timefmt.should_align(86400, "none") is False

    def test_unknown_mode_is_refused(self):
        with pytest.raises(timefmt.InvalidTime) as excinfo:
            timefmt.should_align(3600, "sometimes")
        assert "valid:" in str(excinfo.value)


class TestFloorToStep:
    def test_daily_step_snaps_to_utc_midnight(self):
        afternoon = datetime(2026, 9, 9, 14, 37, 12, tzinfo=timezone.utc).timestamp()
        floored = timefmt.floor_to_step(afternoon, 86400, timezone.utc)
        assert timefmt.iso8601(floored) == "2026-09-09T00:00:00Z"

    def test_daily_step_snaps_to_local_midnight_not_utc_midnight(self):
        # The whole point of `tz`: a daily bucket for an Istanbul reader should
        # start at 00:00 Istanbul (21:00 UTC the previous day), not 00:00 UTC.
        tz = timefmt.zone("Europe/Istanbul")
        afternoon = datetime(2026, 9, 9, 14, 37, tzinfo=timezone.utc).timestamp()
        floored = timefmt.floor_to_step(afternoon, 86400, tz)
        assert timefmt.iso8601(floored) == "2026-09-08T21:00:00Z"

    def test_hourly_step_snaps_to_the_hour(self):
        instant = datetime(2026, 9, 9, 14, 37, 12, tzinfo=timezone.utc).timestamp()
        floored = timefmt.floor_to_step(instant, 3600, timezone.utc)
        assert timefmt.iso8601(floored) == "2026-09-09T14:00:00Z"

    def test_six_hourly_step_snaps_within_the_day(self):
        instant = datetime(2026, 9, 9, 14, 37, tzinfo=timezone.utc).timestamp()
        floored = timefmt.floor_to_step(instant, 6 * 3600, timezone.utc)
        assert timefmt.iso8601(floored) == "2026-09-09T12:00:00Z"

    def test_already_aligned_instant_is_unchanged(self):
        midnight = datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp()
        assert timefmt.floor_to_step(midnight, 86400, timezone.utc) == midnight

    def test_multi_day_step_is_stable_across_calls(self):
        # A 7d bucket counted from "now" would move every request; anchoring it
        # means two reports an hour apart describe the same week.
        tz = timezone.utc
        monday = datetime(2026, 9, 7, 9, 0, tzinfo=tz).timestamp()
        thursday = datetime(2026, 9, 10, 23, 0, tzinfo=tz).timestamp()
        assert timefmt.floor_to_step(monday, 7 * 86400, tz) == timefmt.floor_to_step(
            thursday, 7 * 86400, tz
        )

    def test_daily_alignment_survives_a_dst_transition(self):
        # Istanbul is UTC+3 year-round today, so use a zone that does shift:
        # Europe/Berlin is +2 in summer and +1 in winter. Both must land on
        # local midnight, which means different UTC instants.
        tz = timefmt.zone("Europe/Berlin")
        summer = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).timestamp()
        winter = datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc).timestamp()
        assert timefmt.iso8601(timefmt.floor_to_step(summer, 86400, tz)) == "2026-07-14T22:00:00Z"
        assert timefmt.iso8601(timefmt.floor_to_step(winter, 86400, tz)) == "2026-12-14T23:00:00Z"
