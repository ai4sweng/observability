"""Time handling: instant parsing, output formatting, and step alignment.

Three things live here, all driven by what a caller actually wants from a
`format=series` response.

**Output format.** VictoriaMetrics returns Unix timestamps as floats. Those are
fine for a machine and useless to a human reading a `curl`, so every point
carries both: `timestamp` as ISO-8601 (what the API contract's examples show)
and `unix` as the raw float.

**Instant parsing.** `start`/`end` accept ISO-8601 or a Unix timestamp, and
both have to become a number before any alignment arithmetic can happen.

**Step alignment.** This is the subtle one. VictoriaMetrics places range-query
sample points at `start`, `start + step`, `start + 2*step`, ... So a request
for `period=7d&step=1d` made at 14:37 returns points at 14:37 on each of the
last seven days — not at midnight. For a daily reporting trend that is wrong
in a quiet way: the buckets are days, but not *calendar* days, and two reports
run an hour apart disagree. Aligning `start` down to a calendar boundary in the
caller's timezone fixes it, because every later point then inherits the
alignment.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Steps at or above this are treated as calendar-ish under `align=auto`: an
# hourly or daily bucket is one a human reads off a calendar, so it should
# start on the hour or at midnight. Anything finer (15s, 36s, 5m) has no
# natural calendar boundary worth snapping to.
AUTO_ALIGN_MIN_SECONDS = 3600

VALID_ALIGNMENTS = ("auto", "calendar", "none")


class InvalidTime(ValueError):
    """An unparseable instant, timezone, or alignment mode. Answered with 400."""


def iso8601(unix_seconds: float) -> str:
    """Unix seconds -> `2026-09-09T12:34:56Z`.

    Always rendered in UTC with a `Z` suffix regardless of the request's `tz`:
    the timezone affects where bucket BOUNDARIES fall, not what instant a
    point represents, and a single unambiguous output format keeps clients
    simple.
    """
    return (
        datetime.fromtimestamp(unix_seconds, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def zone(name: str):
    """An IANA timezone by name.

    UTC is special-cased to the stdlib's own `timezone.utc` so the default
    path never depends on a system tz database. That matters: neither Windows
    nor a `python:*-slim` image ships `/usr/share/zoneinfo`, so a bare
    `ZoneInfo("UTC")` raises there. Named zones do need the database, which is
    why `tzdata` is a declared dependency — but if it is somehow missing, only
    an explicit non-UTC `tz` fails, and it fails with an actionable message
    rather than a 500.
    """
    if name.strip().upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise InvalidTime(
            f"unknown timezone '{name}' — use an IANA name such as UTC or "
            f"Europe/Istanbul ({exc})"
        ) from exc


def parse_instant(value: str, field: str) -> float:
    """ISO-8601 or Unix seconds -> Unix seconds.

    A bare ISO timestamp with no offset is read as UTC, matching how
    VictoriaMetrics itself treats one.
    """
    text = (value or "").strip()
    if not text:
        raise InvalidTime(f"{field} is empty")

    try:
        return float(text)
    except ValueError:
        pass

    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise InvalidTime(
            f"{field} must be ISO-8601 (2026-09-08T00:00:00Z) or Unix seconds "
            f"(got '{value}')"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def should_align(step_seconds: int, mode: str) -> bool:
    if mode not in VALID_ALIGNMENTS:
        raise InvalidTime(
            f"unknown align '{mode}'; valid: {', '.join(VALID_ALIGNMENTS)}"
        )
    if mode == "none":
        return False
    if mode == "calendar":
        return True
    return step_seconds >= AUTO_ALIGN_MIN_SECONDS


def floor_to_step(unix_seconds: float, step_seconds: int, tz: ZoneInfo) -> float:
    """Round an instant down to the nearest step boundary in `tz`.

    Whole days and whole weeks snap to local midnight; anything else snaps
    within the local day. Doing the arithmetic in local time is what makes
    a daily bucket start at midnight in Istanbul rather than at 03:00 local
    (midnight UTC) — and it stays correct across a DST change, because the
    offset is re-derived from the local wall clock rather than assumed.
    """
    local = datetime.fromtimestamp(unix_seconds, tz=tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)

    if step_seconds % 86400 == 0:
        days = step_seconds // 86400
        if days > 1:
            # Multi-day buckets are counted forward from an epoch-anchored
            # Monday so successive requests agree on where a bucket starts.
            anchor = datetime(2024, 1, 1, tzinfo=tz)  # a Monday
            elapsed_days = (midnight - anchor).days
            midnight = anchor + timedelta(days=(elapsed_days // days) * days)
        return midnight.timestamp()

    since_midnight = (local - midnight).total_seconds()
    return (midnight + timedelta(seconds=(int(since_midnight) // step_seconds) * step_seconds)).timestamp()
