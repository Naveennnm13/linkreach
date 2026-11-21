"""The 2026 LinkedIn UI: search via company id, top-card-scoped Connect, safe Send."""

from __future__ import annotations

from linkreach import selectors as S
from linkreach.linkedin import LinkedIn, parse_company_id, pick_headline, profile_slug

from tests.fakes import FakePage, FakeSession, company_page, people_search_page, search_page


def _li(env, s):
    return LinkedIn(s, env.db, env.inputs.config, "collect", sleep=lambda _: None)


def test_search_uses_company_id_and_keywords(env):
    s = FakeSession()
    s.add(company_page("microsoft", "1035"))
    s.add(people_search_page("1035", [("Molly O", "Recruiter at Microsoft", "https://www.linkedin.com/in/molly/")]))
    li = _li(env, s)
    out = li.search_company_people("microsoft", "recruiter OR talent")
    assert [c.name for c in out] == ["Molly O"]
    assert "keywords=recruiter+OR+talent" in s.visits[-1] and "currentCompany=%5B%221035%22%5D" in s.visits[-1]
    li.search_company_people("microsoft", "product")
    company_loads = [v for v in s.visits if "/company/microsoft/people/" in v]
    assert len(company_loads) == 1  # id looked up once per company per run


def test_search_falls_back_to_old_people_tab_without_id(env):
    s = FakeSession()
    s.add(search_page("microsoft", [("Ann", "Recruiter", "https://www.linkedin.com/in/ann/")]))
    out = _li(env, s).search_company_people("microsoft", "recruiter")
    assert [c.name for c in out] == ["Ann"]
    assert s.visits[-1].endswith("/people/?keywords=recruiter")


def test_parse_company_id_and_slug():
    assert parse_company_id("/search/results/people/?currentCompany=%5B%221035%22%5D&x=1") == "1035"
    assert parse_company_id('/search/?currentCompany=["42"]') == "42"
    assert parse_company_id(None) is None and parse_company_id("/feed/") is None
    assert profile_slug("https://www.linkedin.com/in/jordan-lee-42/") == "jordan-lee-42"
    assert profile_slug("https://www.linkedin.com/in/a%F0%9F%8C%9F-1/") == "a%F0%9F%8C%9F-1"
    assert profile_slug("https://www.linkedin.com/in/x']/") is None  # never builds a selector from odd text
    assert profile_slug("https://www.linkedin.com/feed/") is None


def test_pick_headline_skips_pronouns_name_and_degree():
    paras = ["He/Him", "· 1st", "· 2nd", "Recruiter at Microsoft", "Seattle"]
    assert pick_headline(paras, "Molly") == "Recruiter at Microsoft"
    assert pick_headline(["Molly", "• 3rd", "Talent Partner"], "Molly") == "Talent Partner"
    assert pick_headline([], "x") == ""


class _NewUIProfile(FakePage):
    """Only this profile's own invite link is clickable; a page-wide invite belongs to someone else."""

    def __init__(self, slug: str):
        super().__init__(f"https://www.linkedin.com/in/{slug}/")
        self.own = S.CONNECT_FOR_SLUG[0].format(slug=slug)
        self.clicked: list[str] = []
        self.dialog = False

    def visible(self):
        e = {self.own: "Connect", S.PROFILE_NAME[0]: "Jordan Lee"}
        if self.dialog:
            e[S.ADD_NOTE_BUTTON[0]] = "Add a note"
            e[S.NOTE_TEXTAREA[0]] = ""
        return e

    def click(self, sel, session):
        self.clicked.append(sel)
        if sel == self.own:
            self.dialog = True
        if sel == S.ADD_NOTE_BUTTON[0]:
            self.dialog = True
        return sel in self.visible()


def test_connect_clicks_this_profiles_own_invite_link(env):
    page = _NewUIProfile("jordan-lee-42")
    s = FakeSession()
    s.add(page)
    s.goto(page.url)
    assert _li(env, s).open_connect_dialog() is None
    assert page.clicked[0] == "[id$='Topcard'] a[href*='custom-invite/?vanityName=jordan-lee-42&']"


def test_connect_selectors_never_reach_outside_the_top_card():
    # Profiles list other people with their own "Invite X to connect" buttons.
    for sel in S.CONNECT_BUTTON + S.CONNECT_FOR_SLUG + S.MORE_BUTTON + S.PENDING_STATE:
        assert sel.startswith(S.TOP) or sel.startswith("section[aria-label='Primary content']"), sel


def test_send_selectors_cannot_match_send_without_a_note():
    for sel in S.SEND_BUTTON:
        assert "has-text('Send')" not in sel and 'has-text("Send")' not in sel, sel


def test_hidden_first_degree_template_does_not_mark_a_stranger_connected(env):
    # Live top cards include a hidden "· 1st" line even for 2nd-degree people.
    s = FakeSession()
    s.add(FakePage("https://www.linkedin.com/in/jordan-lee-42/", elements={
        S.PROFILE_NAME[0]: "Jordan Lee", S.PROFILE_DEGREE[0]: "· 1st",
        S.CONNECT_BUTTON[0]: "Connect", S.PROFILE_ACTIONS[1]: "More",
    }))
    p = _li(env, s).read_profile("https://www.linkedin.com/in/jordan-lee-42/")
    assert p.ok and not p.first_degree


def test_search_title_prefers_current_role_snippet_but_never_past(env):
    from linkreach.linkedin import current_role
    assert current_role("Current: Senior Technical  Recruiter at Microsoft") == "Senior Technical Recruiter at Microsoft"
    assert current_role("Past: Recruiter at Google") == ""
    s = FakeSession()
    s.add(company_page("microsoft", "1035"))
    s.add(people_search_page("1035", []))
    s.pages["https://www.linkedin.com/search/results/people/"].card_list = [
        {"name": "Sarah", "title": "Connecting Talent with Opportunity", "snippet": "Current: Senior Technical Recruiter at Microsoft",
         "url": "https://www.linkedin.com/in/sarah/"},
        {"name": "Pat", "title": "Product Manager at Microsoft", "snippet": "Past: Recruiter at Google", "url": "https://www.linkedin.com/in/pat/"},
    ]
    s.pages["https://www.linkedin.com/search/results/people/"].elements = {S.PEOPLE_CARD[0]: "card"}
    out = _li(env, s).search_company_people("microsoft", "recruiter")
    assert [c.title for c in out] == ["Senior Technical Recruiter at Microsoft", "Product Manager at Microsoft"]
