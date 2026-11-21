"""collect shares each company's slots across buckets and caches company ids."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from linkreach import clock
from linkreach.commands import collect

from tests.conftest import base_session
from tests.fakes import FakePage, company_page, people_search_page
from tests.helpers import profile_for, url

SEARCH = "https://www.linkedin.com/search/results/people/"


def keyword_session(results: dict[str, list[tuple[str, str, str]]]):
    """Fake browser whose people-search page depends on the keywords in the URL.
    results maps a word that appears in the search keywords to the people it returns."""
    s = base_session()
    s.add(company_page("microsoft", "1035"))
    s.add(company_page("salesforce", "2000"))
    pages = {k: people_search_page("x", v) for k, v in results.items()}
    empty = people_search_page("x", [])
    goto = s.goto

    def routed(u: str) -> None:
        if u.startswith(SEARCH):
            kw = parse_qs(urlsplit(u).query).get("keywords", [""])[0].lower()
            s.visits.append(u)
            s.current = next((p for k, p in pages.items() if k in kw), empty)
            return
        goto(u)

    s.goto = routed
    return s


def people(prefix: str, title: str, n: int) -> list[tuple[str, str, str]]:
    return [(f"{prefix} {i}", title, url(f"{prefix.lower()}-{i}")) for i in range(n)]


def test_split_slots():
    assert collect.split_slots(6, ["recruiter", "ai_lead", "leader", "ic"]) == {"recruiter": 2, "ai_lead": 2, "leader": 1, "ic": 1}
    assert collect.split_slots(8, ["a", "b"]) == {"a": 4, "b": 4}
    assert collect.split_slots(3, []) == {}


def _only_microsoft(env):
    env.inputs.companies[:] = [c for c in env.inputs.companies if c.company_name == "Microsoft"]


def test_slots_are_spread_across_buckets(env):
    _only_microsoft(env)
    s = keyword_session({
        "technical recruiter": people("Rec", "Senior Technical Recruiter", 6),
        "ai product": people("Ai", "AI Product Lead", 6),
        "director of product": people("Dir", "Director of Product", 6),
        "senior product manager": people("Pm", "Senior Product Manager", 6),
    })
    for prefix in ("rec", "ai", "dir", "pm"):
        for i in range(6):
            profile_for(s, f"{prefix}-{i}", name=f"{prefix} {i}")
    assert collect.run(env.ctx(s)) == 0
    counts = {}
    for r in env.db.people():
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    assert counts == {"recruiter": 2, "ai_lead": 2, "leader": 1, "ic": 1}


def test_spare_slots_flow_to_the_next_bucket(env):
    _only_microsoft(env)
    s = keyword_session({
        "technical recruiter": people("Rec", "Technical Recruiter", 6),
        "ai product": [],  # nobody in this bucket: its 2 slots pass on
        "director of product": people("Dir", "Director of Product", 6),
        "senior product manager": people("Pm", "Senior Product Manager", 6),
    })
    for prefix in ("rec", "dir", "pm"):
        for i in range(6):
            profile_for(s, f"{prefix}-{i}", name=f"{prefix} {i}")
    collect.run(env.ctx(s))
    counts = {}
    for r in env.db.people():
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    assert counts == {"recruiter": 2, "leader": 3, "ic": 1}


def test_full_bucket_costs_no_page_view(env):
    _only_microsoft(env)
    for i in range(2):  # recruiter share (2 of 6) already used
        env.db.add_person(name=f"R{i}", company="Microsoft", profile_url=url(f"old-{i}"), bucket="recruiter")
    s = keyword_session({"technical recruiter": people("Rec", "Technical Recruiter", 3)})
    collect.run(env.ctx(s))
    assert not any("technical+recruiter" in v for v in s.visits)


def test_full_company_opens_nothing(env):
    _only_microsoft(env)
    for i in range(6):
        env.db.add_person(name=f"R{i}", company="Microsoft", profile_url=url(f"old-{i}"), bucket="recruiter")
    s = keyword_session({})
    collect.run(env.ctx(s))
    assert [v for v in s.visits if "linkedin.com/" in v and "/feed/" not in v] == []


def test_company_id_is_cached_between_runs(env):
    _only_microsoft(env)
    s = keyword_session({})
    collect.run(env.ctx(s))
    assert env.db.get_company_id("microsoft") == "1035"
    s2 = keyword_session({})
    collect.run(env.ctx(s2))
    assert not any("/company/microsoft/people/" in v for v in s2.visits)
    assert any("currentCompany=%5B%221035%22%5D" in v for v in s2.visits)


def test_cached_company_id_expires_after_90_days(env):
    env.db.set_company_id("microsoft", "1035")
    assert env.db.get_company_id("MICROSOFT") == "1035"  # slugs are case-insensitive
    clock.shift(timedelta(days=91))
    try:
        assert env.db.get_company_id("microsoft") is None
    finally:
        clock.reset()


def test_company_id_must_be_numeric(env):
    import pytest

    with pytest.raises(ValueError):
        env.db.set_company_id("microsoft", "1035'); DROP TABLE people;--")
