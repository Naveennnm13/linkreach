"""SQLite storage, status transitions, and activity logging.

Every write commits immediately, so a crash or Ctrl-C loses one action at most.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from linkreach import clock, paths

STATUSES = [
    "collected",
    "needs_detail",
    "drafted",
    "approved",
    "skipped",
    "sent",
    "failed",
    "accepted",
    "followed_up",
    "replied",
    "meeting",
    "referral",
    "dead",
]

# Transitions the tool makes on its own (collect, draft, review, send, sync).
AUTO_TRANSITIONS: dict[str, set[str]] = {
    "collected": {"drafted", "needs_detail", "skipped", "dead"},
    "needs_detail": {"drafted", "needs_detail", "skipped", "dead"},
    "drafted": {"approved", "drafted", "needs_detail", "skipped", "dead"},
    "approved": {"sent", "failed", "skipped", "drafted", "needs_detail", "accepted"},
    "skipped": {"drafted", "needs_detail", "collected", "dead"},
    "sent": {"accepted", "failed", "dead"},
    "failed": {"approved", "drafted", "skipped", "dead"},
    "accepted": {"followed_up", "replied", "meeting", "referral", "dead"},
    "followed_up": {"replied", "meeting", "referral", "dead"},
    "replied": {"meeting", "referral", "dead"},
    "meeting": {"referral", "dead"},
    "referral": {"dead"},
    "dead": set(),
}

# Statuses a person may never be set to by hand (dashboard or `mark`).
# `approved` must go through `review`, which validates the draft first.
MANUAL_FORBIDDEN = {"approved"}

# Statuses that mean "a request went out".
SENT_OR_LATER = {"sent", "accepted", "followed_up", "replied", "meeting", "referral"}
ACCEPTED_OR_LATER = {"accepted", "followed_up", "replied", "meeting", "referral"}

EVENTS = {"view", "send", "fail", "stop", "captcha", "selector_broken", "run", "info"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    title TEXT,
    company TEXT NOT NULL,
    profile_url TEXT NOT NULL UNIQUE,
    bucket TEXT,
    is_alumni INTEGER NOT NULL DEFAULT 0,
    context_1 TEXT,
    context_2 TEXT,
    job_title TEXT,
    status TEXT NOT NULL DEFAULT 'collected',
    note_draft TEXT,
    note_sent TEXT,
    followup_draft TEXT,
    collected_at TEXT NOT NULL,
    sent_at TEXT,
    accepted_at TEXT,
    follow_up_date TEXT,
    followed_up_at TEXT,
    my_notes TEXT,
    -- additions beyond the spec's column list:
    user_detail TEXT,           -- one line of context you typed in `review`
    validation_errors TEXT,     -- why the last draft was rejected
    send_attempted_at TEXT,     -- written BEFORE clicking Send; prevents duplicate sends after a crash
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_people_status ON people(status);
CREATE INDEX IF NOT EXISTS idx_people_company ON people(company);

CREATE TABLE IF NOT EXISTS openers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    opener_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('cli', 'dashboard', 'sync'))
);
CREATE INDEX IF NOT EXISTS idx_history_person ON status_history(person_id);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    command TEXT NOT NULL,
    event TEXT NOT NULL,
    person_id INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_activity_event ON activity_log(event, timestamp);
"""


class TransitionError(ValueError):
    pass


def normalize_profile_url(url: str) -> str:
    """Canonical form used as the unique key: https://www.linkedin.com/in/<slug>/"""
    url = url.strip()
    if url.startswith("/"):
        url = "https://www.linkedin.com" + url
    parts = urlsplit(url)
    path = parts.path
    segs = [s for s in path.split("/") if s]
    if len(segs) >= 2 and segs[0] == "in":
        return f"https://www.linkedin.com/in/{segs[1].lower()}/"
    return f"https://www.linkedin.com{path.rstrip('/').lower()}/"


@dataclass
class DB:
    conn: sqlite3.Connection
    path: Path

    # ---------- lifecycle ----------
    @classmethod
    def open(cls, path: Path | None = None) -> "DB":
        path = path or paths.db_path()
        paths.secure_dir(path.parent)
        conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.commit()
        paths.secure_file(path)
        for suffix in ("-wal", "-shm"):
            paths.secure_file(Path(str(path) + suffix))
        return cls(conn=conn, path=path)

    def close(self) -> None:
        self.conn.close()

    def integrity_ok(self) -> bool:
        row = self.conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"

    # ---------- people ----------
    def has_profile(self, profile_url: str) -> bool:
        url = normalize_profile_url(profile_url)
        return self.conn.execute("SELECT 1 FROM people WHERE profile_url = ?", (url,)).fetchone() is not None

    def add_person(self, **fields: Any) -> int | None:
        """Insert a collected person. Returns the new id, or None if the URL is already known."""
        fields = dict(fields)
        fields["profile_url"] = normalize_profile_url(fields["profile_url"])
        fields.setdefault("status", "collected")
        fields.setdefault("collected_at", clock.now_iso())
        fields["updated_at"] = fields["collected_at"]
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO people ({cols}) VALUES ({marks})",  # nosec B608 - column names are code constants
            tuple(fields.values()),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return None
        pid = int(cur.lastrowid or 0)
        self._history(pid, None, fields["status"], "cli")
        self.conn.commit()
        return pid

    def get(self, person_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()

    def people(self, statuses: Iterable[str] | None = None) -> list[sqlite3.Row]:
        if statuses is None:
            return list(self.conn.execute("SELECT * FROM people ORDER BY id"))
        statuses = list(statuses)
        marks = ", ".join("?" for _ in statuses)
        return list(
            self.conn.execute(
                f"SELECT * FROM people WHERE status IN ({marks}) ORDER BY id",  # nosec B608 - placeholders only
                statuses,
            )
        )

    def update_fields(self, person_id: int, **fields: Any) -> None:
        allowed = {
            "name", "title", "bucket", "is_alumni", "context_1", "context_2", "job_title",
            "note_draft", "note_sent", "followup_draft", "sent_at", "accepted_at",
            "follow_up_date", "followed_up_at", "my_notes", "user_detail",
            "validation_errors", "send_attempted_at",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown field(s): {bad}")
        if not fields:
            return
        fields["updated_at"] = clock.now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE people SET {sets} WHERE id = ?",  # nosec B608 - keys checked against allowlist
            (*fields.values(), person_id),
        )
        self.conn.commit()

    def set_status(
        self,
        person_id: int,
        new_status: str,
        source: str = "cli",
        manual: bool = False,
        follow_up_days: int = 5,
        **fields: Any,
    ) -> None:
        """Change status, apply side-effect timestamps, write status_history, commit."""
        if new_status not in STATUSES:
            raise TransitionError(f"unknown status {new_status!r}")
        row = self.get(person_id)
        if row is None:
            raise TransitionError(f"no person with id {person_id}")
        old = row["status"]
        if manual:
            if new_status in MANUAL_FORBIDDEN:
                raise TransitionError("approve drafts in `linkreach review`, which validates them first")
        elif new_status != old and new_status not in AUTO_TRANSITIONS.get(old, set()):
            raise TransitionError(f"cannot move from {old} to {new_status}")
        if new_status == old and not fields:
            return

        now = clock.now()
        updates: dict[str, Any] = dict(fields)
        if new_status == "sent" and not row["sent_at"] and "sent_at" not in updates:
            updates["sent_at"] = clock.iso(now)
        if new_status in ACCEPTED_OR_LATER and not row["accepted_at"] and "accepted_at" not in updates:
            if new_status == "accepted":
                updates["accepted_at"] = clock.iso(now)
        if new_status == "accepted" and not row["follow_up_date"] and "follow_up_date" not in updates:
            updates["follow_up_date"] = (now.astimezone() + timedelta(days=follow_up_days)).date().isoformat()
        if new_status == "followed_up" and "followed_up_at" not in updates:
            updates["followed_up_at"] = clock.iso(now)
        if new_status == "approved" and "send_attempted_at" not in updates:
            updates["send_attempted_at"] = None

        updates["status"] = new_status
        updates["updated_at"] = clock.iso(now)
        sets = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE people SET {sets} WHERE id = ?",  # nosec B608 - keys are code constants
            (*updates.values(), person_id),
        )
        if new_status != old:
            self._history(person_id, old, new_status, source)
        self.conn.commit()

    def _history(self, person_id: int, old: str | None, new: str, source: str) -> None:
        self.conn.execute(
            "INSERT INTO status_history (person_id, old_status, new_status, changed_at, source) VALUES (?, ?, ?, ?, ?)",
            (person_id, old, new, clock.now_iso(), source),
        )

    def history(self, person_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM status_history WHERE person_id = ? ORDER BY id", (person_id,)
            )
        )

    # ---------- openers ----------
    def add_opener(self, person_id: int, text: str) -> None:
        self.conn.execute(
            "INSERT INTO openers (person_id, opener_text, created_at) VALUES (?, ?, ?)",
            (person_id, text, clock.now_iso()),
        )
        self.conn.commit()

    def recent_openers(self, n: int = 5) -> list[str]:
        rows = self.conn.execute("SELECT opener_text FROM openers ORDER BY id DESC LIMIT ?", (n,))
        return [r[0] for r in rows]

    # ---------- activity ----------
    def log(self, command: str, event: str, person_id: int | None = None, detail: str | None = None) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}")
        self.conn.execute(
            "INSERT INTO activity_log (timestamp, command, event, person_id, detail) VALUES (?, ?, ?, ?, ?)",
            (clock.now_iso(), command, event, person_id, (detail or "")[:500]),
        )
        self.conn.commit()

    def count_events(self, event: str, since_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE event = ? AND timestamp >= ?", (event, since_iso)
        ).fetchone()
        return int(row[0])

    def activity(self, limit: int = 500) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)))

    # ---------- maintenance ----------
    def purge(self, older_than_days: int) -> int:
        cutoff = clock.days_ago_iso(older_than_days)
        ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM people WHERE status IN ('dead', 'skipped') AND COALESCE(updated_at, collected_at) < ?",
                (cutoff,),
            )
        ]
        for pid in ids:
            self.conn.execute("DELETE FROM activity_log WHERE person_id = ?", (pid,))
            self.conn.execute("DELETE FROM status_history WHERE person_id = ?", (pid,))
            self.conn.execute("DELETE FROM openers WHERE person_id = ?", (pid,))
            self.conn.execute("DELETE FROM people WHERE id = ?", (pid,))
        self.conn.commit()
        return len(ids)
