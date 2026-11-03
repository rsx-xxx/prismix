import hashlib
import importlib.util
import json
import threading
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location("bandauth", Path(__file__).parent.parent / "server" / "bandauth.py")
bandauth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bandauth)

ADMIN = "a" * 43


@pytest.fixture()
def cloud(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "tokens.json").write_text(
        json.dumps({"admin_sha256": hashlib.sha256(ADMIN.encode()).hexdigest(), "members": []}))
    app = bandauth.App(tmp_path / "data", tmp_path / "state")
    srv = bandauth.Server(("127.0.0.1", 0), bandauth.make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}")
    yield client, app
    srv.shutdown()
    client.close()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def issue(client, token, label=None):
    return client.post("/api/v1/members", headers=bearer(token), json={"label": label} if label else None)


def test_admin_link_is_not_an_invite(cloud):
    client, _ = cloud
    r = client.get("/join", params={"t": ADMIN})
    assert r.status_code == 403 and "admin" in r.json()["error"]


def test_admin_issues_a_member_link_that_joins(cloud):
    client, app = cloud
    r = issue(client, ADMIN, "Vasya")
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("Vasya-")
    token = body["link"].split("t=")[1]
    assert body["link"].endswith(f"/join?t={token}")
    assert app.tokens.lookup(token) == (body["name"], "member")
    assert client.get("/join", params={"t": token}).status_code == 200


def test_a_member_can_issue_but_not_delete(cloud):
    client, app = cloud
    first = issue(client, ADMIN).json()["link"].split("t=")[1]
    second = issue(client, first, "friend")
    assert second.status_code == 200
    stored = {m["name"]: m for m in app.tokens._data["members"]}
    assert stored[second.json()["name"]]["invited_by"] == app.tokens.lookup(first)[0]
    assert client.delete("/api/v1/songs/AAAAAAAAAAA", headers=bearer(first)).status_code == 403


def test_anonymous_cannot_issue(cloud):
    client, _ = cloud
    assert client.post("/api/v1/members").status_code == 401
    assert issue(client, "b" * 43).status_code == 401


def test_label_is_sanitised(cloud):
    client, _ = cloud
    name = issue(client, ADMIN, "../../é x!").json()["name"]
    assert bandauth.NAME_RE.match(name)


def test_issuing_is_limited_per_issuer(cloud):
    client, _ = cloud
    for _ in range(bandauth.INVITES_PER_HOUR):
        assert issue(client, ADMIN).status_code == 200
    assert issue(client, ADMIN).status_code == 429
