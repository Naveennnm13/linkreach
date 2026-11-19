"""Caps, acceptance brake, auto-send window, and run-stopping exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from linkreach import clock
from linkreach.config import WEEKDAYS, Config
from linkreach.db import ACCEPTED_OR_LATER, DB, SENT_OR_LATER


class StopRun(Exception):
    """LinkedIn showed a CAPTCHA, security check, or warning. Stop at once, no retries."""

    def __init__(self, kind: str, detail: str):
        self.kind = kind  # "captcha" or "warning"
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


class DomainViolation(Exception):
    """The browser tried to navigate somewhere other than LinkedIn."""


class CapReached(Exception):
    def __init__(self, which: str, used: int, cap: int):
        self.which = which
        self.used = used
        self.cap = cap
        super().__init__(f"{which} cap reached ({used}/{cap})")


class SelectorBroken(Exception):
    """Selectors failed on several profiles in a row; LinkedIn's UI probably changed."""


@dataclass
class CapStatus:
    views_today: int
    views_cap: int
    requests_today: int
    requests_cap: int
    requests_week: int
    week_cap: int

    @property
    def views_left(self) -> int:
        return max(0, self.views_cap - self.views_today)

    @property
    def requests_left(self) -> int:
        return max(0, min(self.requests_cap - self.requests_today, self.week_cap - self.requests_week))


def cap_status(db: DB, cfg: Config) -> CapStatus:
    """Counts come from activity_log. A `send` event is written *before* Send is
    clicked, so an attempt that crashed mid-click still counts against the caps."""
    day = clock.start_of_local_day_iso()
    week = clock.days_ago_iso(7)
    return CapStatus(
        views_today=db.count_events("view", day),
        views_cap=cfg.caps.daily_views,
        requests_today=db.count_events("send", day),
        requests_cap=cfg.caps.daily_requests,
        requests_week=db.count_events("send", week),
        week_cap=cfg.caps.weekly_requests,
    )


def check_view_cap(db: DB, cfg: Config) -> None:
    s = cap_status(db, cfg)
    if s.views_today >= s.views_cap:
        raise CapReached("daily view", s.views_today, s.views_cap)


def check_request_caps(db: DB, cfg: Config) -> None:
    s = cap_status(db, cfg)
    if s.requests_today >= s.requests_cap:
        raise CapReached("daily request", s.requests_today, s.requests_cap)
    if s.requests_week >= s.week_cap:
        raise CapReached("7-day request", s.requests_week, s.week_cap)


@dataclass
class BrakeStatus:
    sample: int  # requests older than age_days
    accepted: int
    rate: float | None
    tripped: bool
    min_sample: int
    min_rate: float

    def label(self, overridden: bool = False) -> str:
        if not self.tripped:
            return "off"
        return "overridden" if overridden else "on"


def brake_status(db: DB, cfg: Config) -> BrakeStatus:
    cutoff = clock.days_ago_iso(cfg.brake.age_days)
    sent_states = sorted(SENT_OR_LATER | {"dead"})
    marks = ", ".join("?" for _ in sent_states)
    rows = db.conn.execute(
        f"SELECT status, accepted_at FROM people WHERE sent_at IS NOT NULL AND sent_at < ? AND status IN ({marks})",  # nosec B608
        (cutoff, *sent_states),
    ).fetchall()
    sample = len(rows)
    accepted = sum(1 for r in rows if r["accepted_at"] or r["status"] in ACCEPTED_OR_LATER)
    rate = (accepted / sample) if sample else None
    tripped = sample >= cfg.brake.min_sample and rate is not None and rate < cfg.brake.min_rate
    return BrakeStatus(sample, accepted, rate, tripped, cfg.brake.min_sample, cfg.brake.min_rate)


def overall_acceptance(db: DB) -> tuple[int, int, float | None]:
    sent_states = sorted(SENT_OR_LATER | {"dead"})
    marks = ", ".join("?" for _ in sent_states)
    rows = db.conn.execute(
        f"SELECT status, accepted_at FROM people WHERE sent_at IS NOT NULL AND status IN ({marks})",  # nosec B608
        sent_states,
    ).fetchall()
    sent = len(rows)
    acc = sum(1 for r in rows if r["accepted_at"] or r["status"] in ACCEPTED_OR_LATER)
    return sent, acc, (acc / sent if sent else None)


def in_auto_window(cfg: Config, when: datetime | None = None) -> bool:
    local = (when or clock.now()).astimezone()
    day = WEEKDAYS[local.weekday()]
    if day not in cfg.auto_window.days:
        return False
    t = local.time().replace(tzinfo=None)
    return cfg.auto_window.start <= t < cfg.auto_window.end
