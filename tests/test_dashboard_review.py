"""Review queue, approve/decline, editing, redraft, outbox, and live status."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from linkreach.dashboard.server import create_app
from linkreach.drafting import Drafter

from tests.fakes import FakeClaude
from tests.helpers import add_approved, add_drafted, url

TOKEN = "r" * 43
H = {"X-Linkreach-Token": TOKEN}


@pytest.fixture
def client(env):
    app = create_app(env.inputs, env.db.path, token=TOKEN, drafter_factory=lambda: Drafter(env.inputs.config, client=FakeClaude()))
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_review_queue_lists_drafted_first(client, env):
    thin = env.db.add_person(name="Thin", company="Microsoft", profile_url=url("thin"))
    env.db.set_status(thin, "needs_detail")
    ready = add_drafted(env, "ready")
    items = client.get("/api/review", headers=H).json()["items"]
    assert [i["id"] for i in items] == [ready, thin]
    assert items[0]["note_chars"] == len(env.db.get(ready)["note_draft"])
    assert items[0]["followup_words"] > 50


def test_all_review_endpoints_need_token(client, env):
    pid = add_drafted(env, "t")
    for path, body in (
        ("/api/review", None), ("/api/outbox", None),
    ):
        assert client.get(path).status_code == 403
    for path in ("approve", "decline", "unapprove", "draft", "redraft"):
        assert client.post(f"/api/people/{pid}/{path}", json={"note": "x", "followup": "y", "detail": "z"}).status_code == 403
    assert client.post("/api/validate", json={"note": "x", "followup": "y"}).status_code == 403
    assert env.db.get(pid)["status"] == "drafted"


def test_approve_valid_draft(client, env):
    pid = add_drafted(env, "a")
    r = client.post(f"/api/people/{pid}/approve", headers=H)
    assert r.status_code == 200
    assert env.db.get(pid)["status"] == "approved"
    h = env.db.history(pid)[-1]
    assert (h["old_status"], h["new_status"], h["source"]) == ("drafted", "approved", "dashboard")


def test_approve_revalidates_and_rejects(client, env):
    pid = add_drafted(env, "b")
    env.db.update_fields(pid, note_draft="Hi B, see https://evil.example. Would like to connect.")
    r = client.post(f"/api/people/{pid}/approve", headers=H)
    assert r.status_code == 422
    assert any("URL" in e for e in r.json()["errors"])
    row = env.db.get(pid)
    assert row["status"] == "needs_detail" and "URL" in row["validation_errors"]


def test_needs_detail_cannot_be_approved(client, env):
    pid = env.db.add_person(name="N", company="Microsoft", profile_url=url("n"))
    env.db.set_status(pid, "needs_detail")
    assert client.post(f"/api/people/{pid}/approve", headers=H).status_code == 409
    assert env.db.get(pid)["status"] == "needs_detail"


def test_decline_and_undo(client, env):
    pid = add_drafted(env, "c")
    assert client.post(f"/api/people/{pid}/decline", headers=H).status_code == 200
    assert env.db.get(pid)["status"] == "skipped"
    # undo puts it back via the normal status endpoint
    assert client.post(f"/api/people/{pid}/status", headers=H, json={"status": "drafted"}).status_code == 200
    assert env.db.get(pid)["status"] == "drafted"


def test_cannot_decline_sent(client, env):
    pid = add_approved(env, "s")
    env.db.set_status(pid, "sent")
    assert client.post(f"/api/people/{pid}/decline", headers=H).status_code == 409


def test_unapprove_only_before_send_attempt(client, env):
    pid = add_approved(env, "u")
    assert client.post(f"/api/people/{pid}/unapprove", headers=H).status_code == 200
    assert env.db.get(pid)["status"] == "drafted"
    env.db.set_status(pid, "approved")
    env.db.update_fields(pid, send_attempted_at="2026-11-19T10:00:00+00:00")
    assert client.post(f"/api/people/{pid}/unapprove", headers=H).status_code == 409


def test_edit_draft_validates(client, env):
    pid = add_drafted(env, "e")
    bad = client.post(f"/api/people/{pid}/draft", headers=H, json={"note": "Hi E, call 617-555-0199", "followup": "Dear E"})
    assert bad.json()["ok"] is False and env.db.get(pid)["status"] == "needs_detail"
    good_note = "Hi E, your onboarding post was a delight. I'm a PM with 3+ years in AI SaaS. Would like to connect."
    good = client.post(f"/api/people/{pid}/draft", headers=H, json={"note": good_note, "followup": "Dear E,\n\nThanks.\n\nBest,\nNaveen"})
    assert good.json()["ok"] is True
    row = env.db.get(pid)
    assert row["status"] == "drafted" and row["note_draft"] == good_note
    assert client.post(f"/api/people/{pid}/approve", headers=H).status_code == 200


def test_validate_preview_does_not_save(client, env):
    pid = add_drafted(env, "v")
    before = env.db.get(pid)["note_draft"]
    r = client.post("/api/validate", headers=H, json={"note": "x" * 300, "followup": "ok"}).json()
    assert r["ok"] is False and r["note_chars"] == 300
    assert env.db.get(pid)["note_draft"] == before


def test_redraft_with_detail(client, env):
    pid = env.db.add_person(name="Sam Lee", company="Microsoft", profile_url=url("sam"), bucket="ic", context_1="")
    env.db.set_status(pid, "needs_detail")
    r = client.post(f"/api/people/{pid}/redraft", headers=H, json={"detail": "spoke at Build about Copilot pricing"})
    assert r.status_code == 200 and r.json()["status"] == "drafted"
    row = env.db.get(pid)
    assert row["status"] == "drafted" and row["user_detail"] == "spoke at Build about Copilot pricing"
    assert env.db.history(pid)[-1]["source"] == "dashboard"


def test_outbox_shows_queue_in_send_order_and_sent_notes(client, env):
    sf = add_approved(env, "sf", company="Salesforce")
    al = add_approved(env, "al", company="Salesforce", alumni=1)
    sent = add_approved(env, "done")
    env.db.set_status(sent, "sent", note_sent="the exact note")
    r = client.get("/api/outbox", headers=H).json()
    assert [p["id"] for p in r["queued"]] == [al, sf]
    assert r["sent"][0]["note_sent"] == "the exact note"
    assert r["requests_left_today"] == 20


def test_summary_live_status(client, env):
    env.db.log("send", "run", None, "start:manual")
    pid = add_drafted(env, "live")
    env.db.log("send", "view", pid, "/in/live/")
    s = client.get("/api/summary", headers=H).json()
    assert s["running"] == ["send"]
    assert s["last_event"]["event"] == "view" and s["last_event"]["name"] == "Jane Doe"
    assert s["counts"]["drafted"] == 1
    env.db.log("send", "run", None, "end:ok")
    assert client.get("/api/summary", headers=H).json()["running"] == []
