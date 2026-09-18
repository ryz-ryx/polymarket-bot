import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import dashboard
from dashboard import DashboardHandler


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(dashboard.config, "control_secret", "s3cret", raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _req(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_post_control_requires_secret(server):
    assert _req(server + "/api/control", "POST", {"paused": True}) == 401


def test_post_control_rejects_non_bool(server):
    h = {"X-Control-Secret": "s3cret", "Content-Type": "application/json"}
    assert _req(server + "/api/control", "POST", {"pause": True}, h) == 400


def test_read_auth_enforced_when_enabled(server, monkeypatch):
    monkeypatch.setenv("REQUIRE_READ_AUTH", "true")
    assert _req(server + "/api/control") == 401
    assert _req(server + "/api/control", headers={"X-Control-Secret": "s3cret"}) == 200


def test_read_open_by_default(server, monkeypatch):
    monkeypatch.delenv("REQUIRE_READ_AUTH", raising=False)
    assert _req(server + "/api/control") == 200
