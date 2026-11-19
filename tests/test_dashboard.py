from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from linkreach.dashboard.server import INDEX, create_app
from linkreach.drafting import Drafter

from tests.fakes import FakeClaude
from tests.helpers import add_approved, add_drafted, url

TOKEN = "t" * 43


@pytest.fixture
def client(env):
    app = create_app(env.inputs, env.db.path, token=TOKEN, drafter_factory=lambda: Drafter(env.inputs.config, client=FakeClaude()))
    return TestClient(app, base_url="http://127.0.0.1:8765")


H = {"X-Linkreach-Token": TOKEN}


def test_no_token_is_403(client):
    assert client.get("/api/people").status_code == 403
    assert client.get("/api/people", headers={"X-Linkreach-Token": "wrong"}).status_code == 403
    assert client.post("/api/people/1/status", json={"status": "dead"}).status_code == 403


def test_foreign_host_is_403(client):
    r = client.get("/api/people", headers={**H, "Host": "evil.example:8765"})
    assert r.status_code == 403
    r = client.get("/", headers={"Host": "attacker.test"})
    assert r.status_code == 403
    assert client.get("/api/people", headers={**H, "Host": "localhost:8765"}).status_code == 200


def test_foreign_origin_is_403(client):
    assert client.get("/api/people", headers={**H, "Origin": "https://evil.example"}).status_code == 403


def test_no_cors_headers(client):
    r = client.get("/api/summary", headers={**H, "Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200
    assert not any(k.lower().startswith("access-control-") for k in r.headers)
    r = client.options("/api/summary", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert not any(k.lower().startswith("access-control-") for k in r.headers)


def test_state_changes_are_post_only(client, env):
    pid = add_drafted(env, "x")
    assert client.get(f"/api/people/{pid}/status", headers=H).status_code == 405
    assert client.put(f"/api/people/{pid}/status", headers=H, json={"status": "dead"}).status_code == 405


def test_csp_and_security_headers(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert csp.startswith("default-src 'self'")
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'<script nonce="{nonce}">' in r.text and f'<style nonce="{nonce}">' in r.text
    assert r.headers["x-frame-options"] == "DENY"
    api = client.get("/api/summary", headers=H)
    assert api.headers["content-security-policy"].startswith("default-src 'self'")


def test_page_never_uses_innerhtml():
    src = INDEX.read_text(encoding="utf-8")
    script = src[src.index("<script"):]
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in script.replace("No innerHTML anywhere", ""), sink


def test_script_name_returned_as_plain_data(client, env):
    env.db.add_person(name="<script>alert(1)</script>", company="Microsoft", profile_url=url("xss"))
    r = client.get("/api/people", headers=H)
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()[0]["name"] == "<script>alert(1)</script>"


def test_status_change_writes_history_and_cli_sees_it(client, env, capsys):
    from linkreach.commands import misc

    pid = add_approved(env, "jane")
    env.db.set_status(pid, "sent")
    r = client.post(f"/api/people/{pid}/status", headers=H, json={"status": "accepted"})
    assert r.status_code == 200, r.text
    hist = env.db.history(pid)
    assert (hist[-1]["old_status"], hist[-1]["new_status"], hist[-1]["source"]) == ("sent", "accepted", "dashboard")
    misc.list_people(env.ctx(), "accepted")  # `linkreach list --status accepted`
    assert "1 people" in capsys.readouterr().out
    assert env.db.get(pid)["follow_up_date"]


def test_dashboard_cannot_approve(client, env):
    pid = add_drafted(env, "y")
    r = client.post(f"/api/people/{pid}/status", headers=H, json={"status": "approved"})
    assert r.status_code == 400
    assert env.db.get(pid)["status"] == "drafted"


def test_followups_done_and_snooze(client, env):
    pid = add_approved(env, "f")
    env.db.update_fields(pid, job_title="Product Manager II")
    env.db.set_status(pid, "sent")
    env.db.set_status(pid, "accepted")
    env.db.update_fields(pid, follow_up_date="2000-01-01")
    items = client.get("/api/followups", headers=H).json()
    assert [i["id"] for i in items] == [pid]
    assert items[0]["email_subject"] == "Product Manager II - Naveen Murali"
    snoozed = client.post(f"/api/people/{pid}/followup/snooze", headers=H).json()["follow_up_date"]
    assert snoozed > "2000-01-01"
    assert client.get("/api/followups", headers=H).json() == []
    env.db.update_fields(pid, follow_up_date="2000-01-01")
    assert client.post(f"/api/people/{pid}/followup/done", headers=H).status_code == 200
    row = env.db.get(pid)
    assert row["status"] == "followed_up" and row["followed_up_at"]


def test_regenerate_followup(client, env):
    pid = add_approved(env, "g")
    r = client.post(f"/api/people/{pid}/followup/regenerate", headers=H)
    assert r.status_code == 200 and r.json()["followup_draft"].startswith("Dear Jane")


def test_summary_companies_insights_activity(client, env):
    pid = add_approved(env, "h")
    env.db.set_status(pid, "sent")
    env.db.log("collect", "run", None, "end:stopped:captcha")
    s = client.get("/api/summary", headers=H).json()
    assert s["daily_cap"] == 20 and s["weekly_cap"] == 80
    assert s["brake"]["status"] == "off"
    assert s["banner"]["reason"] == "captcha"
    comps = client.get("/api/companies", headers=H).json()
    assert comps[0]["company"] == "Microsoft" and comps[0]["sent"] == 1
    ins = client.get("/api/insights", headers=H).json()
    assert ins["funnel"][0] == {"stage": "collected", "count": 1}
    assert ins["by_bucket"][0]["bucket"] == "recruiter"
    acts = client.get("/api/activity", headers=H).json()
    assert acts[0]["detail"] == "end:stopped:captcha"


def test_company_flag_after_five_sends_no_accepts(client, env):
    for i in range(5):
        pid = add_approved(env, f"z{i}")
        env.db.set_status(pid, "sent")
    comps = {c["company"]: c for c in client.get("/api/companies", headers=H).json()}
    assert comps["Microsoft"]["flag"] is True
