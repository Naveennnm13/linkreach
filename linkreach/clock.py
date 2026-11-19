"""Single source of time so tests can freeze or shift it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_offset = timedelta(0)
_frozen: datetime | None = None


def now() -> datetime:
    """Current time, timezone-aware UTC."""
    if _frozen is not None:
        return _frozen
    return datetime.now(timezone.utc) + _offset


def now_iso() -> str:
    return iso(now())


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_now() -> datetime:
    return now().astimezone()


def start_of_local_day_iso() -> str:
    local = local_now()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return iso(midnight)


def days_ago_iso(days: float) -> str:
    return iso(now() - timedelta(days=days))


def freeze(dt: datetime | None) -> None:
    global _frozen
    _frozen = dt


def shift(delta: timedelta) -> None:
    global _offset
    _offset += delta


def reset() -> None:
    global _offset, _frozen
    _offset = timedelta(0)
    _frozen = None
