from prismix import hflogin


def test_state_reports_signed_in_when_a_token_exists(monkeypatch):
    monkeypatch.setattr(hflogin, "_state", {"status": "idle"})
    monkeypatch.setattr(hflogin, "logged_in", lambda: True)
    assert hflogin.state()["status"] == "done"


def test_start_returns_the_code_and_waits(monkeypatch):
    import huggingface_hub.utils._oauth_device as dev

    monkeypatch.setattr(hflogin, "_state", {"status": "idle"})
    monkeypatch.setattr(dev, "request_device_code", lambda: {"verification_uri_complete": "https://hf.co/x", "user_code": "ABCD"})
    monkeypatch.setattr(hflogin.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    out = hflogin.start()
    assert out["status"] == "waiting" and out["code"] == "ABCD" and out["url"] == "https://hf.co/x"
