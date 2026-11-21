from __future__ import annotations

from linkreach.commands import collect
from linkreach.commands.common import EXIT_SELECTORS, EXIT_STOPPED
from linkreach.linkedin import LinkedIn
from linkreach.safety import CapReached

from tests.conftest import base_session
from tests.fakes import FakePage, captcha_page, search_page
from tests.helpers import profile_for, url


def test_dry_run_prints_and_opens_nothing(env, capsys):
    ctx = env.ctx(session=None)  # factory raises if called
    assert collect.run(ctx, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Planned searches" in out
    assert "Microsoft" in out and "Salesforce" in out
    assert "search/results/people/?keywords=" in out and "<microsoft id>" in out
    assert ctx.factory_calls["n"] == 0
    assert env.db.count_events("view", "0000") == 0


def test_dry_run_order_alumni_then_priority(env):
    plans = collect.plan(env.inputs.config, env.inputs.companies)
    assert plans[0].company.company_name == "Microsoft" and plans[0].bucket_hint == "alumni"
    assert all(p.company.priority == 1 for p in plans[: len(plans) // 2])


def test_collect_saves_person_with_context_and_alumni(env):
    s = base_session()
    s.default = search_page("x", [])
    s.add(search_page("microsoft", [
        ("Jane Doe", "Senior Technical Recruiter", url("jane-doe")),
        ("Ivan Intern", "Product Intern", url("ivan")),          # excluded title
        ("Fred Finance", "Finance Manager", url("fred")),        # no bucket
    ]))
    profile_for(s, "jane-doe", name="Jane Doe", headline="Technical Recruiter, Azure",
                about="I hire early-career PMs for Azure.", education="Northeastern University | MS")
    assert collect.run(env.ctx(s)) == 0
    rows = env.db.people()
    assert len(rows) == 1
    r = rows[0]
    assert (r["name"], r["bucket"], r["is_alumni"], r["status"]) == ("Jane Doe", "recruiter", 1, "collected")
    assert r["context_1"] == "I hire early-career PMs for Azure."
    assert r["job_title"] == "Product Manager II"
    assert url("ivan") not in s.visits and url("fred") not in s.visits


def test_classify_and_excludes(env):
    cfg = env.inputs.config
    assert collect.classify("Director of Product, Copilot", cfg) == "leader"
    assert collect.classify("Group Product Manager", cfg) == "ic"
    assert collect.classify("AI Product Lead", cfg) == "ai_lead"
    assert collect.classify("Talent Acquisition Coordinator", cfg) is None
    assert collect.classify("Associate Product Manager", cfg) is None


def test_captcha_stops_run_and_logs(env, capsys):
    s = base_session()
    s.add(captcha_page("https://www.linkedin.com/company/microsoft/people/"))
    code = collect.run(env.ctx(s))
    assert code == EXIT_STOPPED
    ev = [r["event"] for r in env.db.activity()]
    assert "captcha" in ev
    assert len(s.visits) == 1  # stopped at once, no retries
    assert "STOPPED" in capsys.readouterr().out
    last_run = env.db.conn.execute("SELECT detail FROM activity_log WHERE event='run' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert last_run == "end:stopped:captcha"


def test_checkpoint_redirect_is_a_captcha(env):
    s = base_session()
    s.add(FakePage("https://www.linkedin.com/company/microsoft/people/", body=""))
    s.pages["https://www.linkedin.com/company/microsoft/people/"].url = "https://www.linkedin.com/checkpoint/challenge/xyz"
    assert collect.run(env.ctx(s)) == EXIT_STOPPED
    assert any(r["event"] == "captcha" for r in env.db.activity())


def test_restriction_warning_stops(env):
    s = base_session()
    s.add(FakePage("https://www.linkedin.com/company/microsoft/people/", body="We noticed unusual activity on your account"))
    assert collect.run(env.ctx(s)) == EXIT_STOPPED
    assert any(r["event"] == "stop" for r in env.db.activity())


def test_selector_broken_after_three(env):
    s = base_session()
    s.default = search_page("x", [])
    people = [(f"P{i}", "Technical Recruiter", url(f"p{i}")) for i in range(5)]
    s.add(search_page("microsoft", people))
    for i in range(5):
        profile_for(s, f"p{i}", broken=True)
    code = collect.run(env.ctx(s))
    assert code == EXIT_SELECTORS
    assert any(r["event"] == "selector_broken" for r in env.db.activity())
    assert s.screenshots and s.screenshots[0].exists()
    assert sum(1 for v in s.visits if "/in/p" in v) == 3


def test_view_cap_stops_collect(env):
    for _ in range(40):
        env.db.log("collect", "view", None, "seed")
    s = base_session()
    li = LinkedIn(s, env.db, env.inputs.config, "collect", sleep=lambda x: None)
    try:
        li.visit("https://www.linkedin.com/feed/")
        raise AssertionError("expected CapReached")
    except CapReached:
        pass
    assert s.visits == []
    assert collect.run(env.ctx(s)) == 0  # clean stop for the day
    assert s.visits == []


def test_random_delay_between_page_loads(env):
    waits: list[float] = []
    s = base_session()
    li = LinkedIn(s, env.db, env.inputs.config, "collect", sleep=waits.append)
    li.visit("https://www.linkedin.com/feed/")
    li.visit("https://www.linkedin.com/feed/")
    li.visit("https://www.linkedin.com/feed/")
    assert len(waits) == 2 and all(8 <= w <= 25 for w in waits)
