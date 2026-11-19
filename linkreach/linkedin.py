"""LinkedIn page logic on top of a `browser.Session`.

Every page load goes through `LinkedIn.visit`, which:
1. checks the daily view cap,
2. waits a random 8 to 25 seconds after the previous load,
3. loads the page and checks the domain allowlist,
4. writes a `view` event to the activity log,
5. stops the run on any CAPTCHA, security check, or warning.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote_plus

from linkreach import logsetup, sanitize
from linkreach import selectors as S
from linkreach.browser import Session
from linkreach.config import Config
from linkreach.db import DB, normalize_profile_url
from linkreach.safety import StopRun, check_view_cap

log = logsetup.get("linkedin")
_rng = random.SystemRandom()


@dataclass
class Candidate:
    name: str
    title: str
    profile_url: str


@dataclass
class Profile:
    ok: bool
    name: str = ""
    headline: str = ""
    about: str = ""
    featured: str = ""
    education: str = ""
    first_degree: bool = False
    pending: bool = False
    has_actions: bool = False
    missing: list[str] = field(default_factory=list)


@dataclass
class ConnectResult:
    outcome: str  # sent | failed | skipped | already_pending | already_connected
    reason: str = ""


class LinkedIn:
    def __init__(
        self,
        session: Session,
        db: DB,
        cfg: Config,
        command: str,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.s = session
        self.db = db
        self.cfg = cfg
        self.command = command
        self.sleep = sleep
        self._loaded_once = False

    # ---------- navigation ----------
    def visit(self, url: str, person_id: int | None = None) -> None:
        check_view_cap(self.db, self.cfg)
        if self._loaded_once:
            self.sleep(_rng.uniform(self.cfg.delay.min_seconds, self.cfg.delay.max_seconds))
        self._loaded_once = True
        self.s.goto(url)
        self.db.log(self.command, "view", person_id, _url_for_log(url))
        self.detect_stop()

    def detect_stop(self) -> None:
        url = (self.s.url or "").lower()
        for kind, parts in S.STOP_URL_PARTS.items():
            for part in parts:
                if part in url:
                    self._stop(kind, f"url matched {part!r}")
        for kind, sels in S.STOP_SELECTORS.items():
            if self.s.exists(sels):
                self._stop(kind, "page element matched")
        body = (self.s.body_text() or "").lower().replace("’", "'")
        for kind, phrases in S.STOP_PHRASES.items():
            for phrase in phrases:
                if phrase in body:
                    self._stop(kind, f"page text matched {phrase!r}")

    def _stop(self, kind: str, detail: str) -> None:
        event = "captcha" if kind == "captcha" else "stop"
        self.db.log(self.command, event, None, f"{kind}: {detail}")
        logsetup.event(log, "run stopped by LinkedIn page", kind=kind, detail=detail, command=self.command)
        raise StopRun(kind, detail)

    # ---------- login ----------
    def logged_in(self) -> bool:
        self.visit(S.FEED_URL)
        url = (self.s.url or "").lower()
        if any(p in url for p in S.LOGGED_OUT_URL_PARTS):
            return False
        return self.s.exists(S.LOGGED_IN_MARKERS, timeout_ms=5000)

    # ---------- search ----------
    def search_company_people(self, company_slug: str, keywords: str) -> list[Candidate]:
        url = S.COMPANY_PEOPLE_URL.format(slug=quote_plus(company_slug), keywords=quote_plus(keywords))
        self.visit(url)
        self.s.exists(S.PEOPLE_CARD, timeout_ms=8000)
        out: list[Candidate] = []
        for rec in self.s.cards(S.PEOPLE_CARD, S.PEOPLE_CARD_FIELDS):
            href = rec.get("url", "")
            if "/in/" not in href:
                continue  # "LinkedIn Member" cards without a public profile
            name = sanitize.clean_line(rec.get("name", ""), 120)
            if not name or name.lower() == "linkedin member":
                continue
            out.append(
                Candidate(
                    name=name,
                    title=sanitize.clean_line(rec.get("title", ""), 200),
                    profile_url=normalize_profile_url(href),
                )
            )
        return out

    # ---------- profile ----------
    def read_profile(self, url: str, person_id: int | None = None) -> Profile:
        self.visit(url, person_id)
        name = self.s.text(S.PROFILE_NAME, timeout_ms=8000)
        p = Profile(ok=False)
        if not name:
            p.missing.append("PROFILE_NAME")
        p.has_actions = self.s.exists(S.PROFILE_ACTIONS, timeout_ms=3000)
        if not p.has_actions:
            p.missing.append("PROFILE_ACTIONS")
        p.name = sanitize.clean_line(name or "", 120)
        p.headline = sanitize.clean(self.s.text(S.PROFILE_HEADLINE) or "")
        p.about = sanitize.clean(self.s.text(S.PROFILE_ABOUT) or "")
        feats = self.s.texts(S.PROFILE_FEATURED)
        p.featured = sanitize.clean(feats[0] if feats else "")
        p.education = sanitize.clean(" | ".join(self.s.texts(S.PROFILE_EDUCATION)[:12]), 1000)
        degree = (self.s.text(S.PROFILE_DEGREE) or "").lower()
        p.first_degree = "1st" in degree
        p.pending = self.s.exists(S.PENDING_STATE)
        p.ok = not p.missing
        return p

    # ---------- connect ----------
    def open_connect_dialog(self) -> ConnectResult | None:
        """Click Connect (or More > Connect) and then 'Add a note'.
        Returns a ConnectResult on failure, None when the note box is ready."""
        if self.s.exists(S.PENDING_STATE):
            return ConnectResult("already_pending")
        if not self.s.click(S.CONNECT_BUTTON, timeout_ms=3000):
            if not self.s.click(S.MORE_BUTTON, timeout_ms=3000):
                return ConnectResult("failed", "no Connect or More button")
            if not self.s.click(S.MORE_MENU_CONNECT, timeout_ms=3000):
                self.s.press("Escape")
                return ConnectResult("failed", "no Connect option under More")
        self.detect_stop()
        if self.s.exists(S.EMAIL_REQUIRED, timeout_ms=1500):
            self._dismiss()
            return ConnectResult("failed", "LinkedIn asked for their email address")
        if not self.s.click(S.ADD_NOTE_BUTTON, timeout_ms=5000):
            self._dismiss()
            return ConnectResult("failed", "no 'Add a note' button")
        if not self.s.exists(S.NOTE_TEXTAREA, timeout_ms=5000):
            self._dismiss()
            return ConnectResult("failed", "note box did not appear")
        return None

    def type_note(self, note: str) -> bool:
        return self.s.fill(S.NOTE_TEXTAREA, note)

    def click_send(self) -> bool:
        return self.s.click(S.SEND_BUTTON, timeout_ms=5000)

    def confirm_pending(self, timeout_ms: int = 10000) -> bool:
        ok = self.s.exists(S.PENDING_STATE, timeout_ms=timeout_ms)
        self.detect_stop()
        return ok

    def _dismiss(self) -> None:
        if not self.s.click(S.DIALOG_DISMISS, timeout_ms=1500):
            self.s.press("Escape")


def _url_for_log(url: str) -> str:
    # Keep only the path; no query strings in the activity log.
    return url.split("?", 1)[0][:300]
