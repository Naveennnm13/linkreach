"""Opening the dashboard: one-time /open?code= link that redirects to the token fragment."""

from __future__ import annotations

from fastapi.testclient import TestClient

from linkreach.dashboard import server
from linkreach.dashboard.server import create_app

TOKEN = "o" * 43


def _client(env):
    app = create_app(env.inputs, env.db.path, token=TOKEN)
    return app, TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False)


def test_launch_code_redirects_to_token_once(env):
    app, c = _client(env)
    code = app.state.launch_code
    assert code and code != TOKEN
    r = c.get(f"/open?code={code}")
    assert r.status_code == 303 and r.headers["location"] == f"/#token={TOKEN}"
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    assert c.get(f"/open?code={code}").status_code == 403  # single use


def test_launch_code_rejects_wrong_missing_and_expired(env, monkeypatch):
    app, c = _client(env)
    assert c.get("/open?code=nope").status_code == 403
    assert c.get("/open").status_code == 403
    monkeypatch.setattr(server.time, "monotonic", lambda: app.state.launch_expires + 1)
    assert c.get(f"/open?code={app.state.launch_code}").status_code == 403


def test_launch_link_still_needs_local_host(env):
    app, _ = _client(env)
    evil = TestClient(app, base_url="http://evil.example", follow_redirects=False)
    assert evil.get(f"/open?code={app.state.launch_code}").status_code == 403
    assert app.state.launch_code  # not consumed by the refused request


def test_launch_code_is_not_the_api_token(env):
    app, c = _client(env)
    assert c.get("/api/summary", headers={"X-Linkreach-Token": app.state.launch_code}).status_code == 403


def test_serve_refuses_busy_port_before_opening_browser(env, monkeypatch):
    import pytest

    opened = []
    monkeypatch.setattr(server, "port_free", lambda *a: False)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    with pytest.raises(server.PortInUse):
        server.serve(env.inputs, env.db.path)
    assert opened == []
