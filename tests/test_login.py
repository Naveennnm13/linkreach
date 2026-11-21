"""`linkreach login` opens plain Chrome, then confirms sign-in under the filtered session."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkreach import selectors as S
from linkreach.browser import ProfileGuardError, default_chrome_dirs, open_plain_chrome
from linkreach.commands import misc
from linkreach.linkedin import LinkedIn
from linkreach.safety import DomainViolation

from tests.fakes import FakePage, FakeSession, feed_page


def _ctx(env, session, opened):
    ctx = env.ctx(session=session)
    ctx.open_chrome = lambda profile, url: opened.append((profile, url)) or 0
    return ctx


def test_login_opens_plain_chrome_on_dedicated_profile_then_checks_feed(env):
    opened: list = []
    s = FakeSession()
    s.add(feed_page(logged_in=True))
    assert misc.login(_ctx(env, s, opened)) == 0
    assert opened == [(env.inputs.config.profile_path, S.LOGIN_URL)]
    assert s.visits == [S.FEED_URL] and s.closed


def test_login_reports_failure_when_feed_redirects_to_login(env):
    s = FakeSession(default=feed_page(logged_in=False))
    assert misc.login(_ctx(env, s, [])) == 1


def test_new_ui_marker_counts_as_signed_in(env):
    # LinkedIn's current UI has scrambled classes; only href-based markers match.
    s = FakeSession()
    s.add(FakePage(S.FEED_URL, body="Feed", elements={'a[href*="/messaging/"]': "Messaging"}))
    li = LinkedIn(s, env.db, env.inputs.config, "doctor", sleep=lambda _: None)
    assert li.logged_in()


def test_plain_chrome_refuses_default_profile_and_other_sites(tmp_path: Path):
    with pytest.raises(ProfileGuardError):
        open_plain_chrome(default_chrome_dirs()[0] / "Default", S.LOGIN_URL)
    with pytest.raises(DomainViolation):
        open_plain_chrome(tmp_path / "profile", "https://example.com/")
