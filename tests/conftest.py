import pytest

from prismix import band, tagging


@pytest.fixture(autouse=True)
def testclient_is_local(monkeypatch):
    """Starlette's TestClient reports host "testclient"; treat it as the desktop host."""
    monkeypatch.setattr(band, "is_loopback", lambda h: h == "testclient")
    monkeypatch.setattr(tagging, "ONLINE", False)
