import pytest

from prismix import band, cloud, prefetch, tagging


@pytest.fixture(autouse=True)
def testclient_is_local(monkeypatch):
    """Starlette's TestClient reports host "testclient"; treat it as the desktop host."""
    monkeypatch.setattr(band, "is_loopback", lambda h: h == "testclient")
    monkeypatch.setattr(tagging, "ONLINE", False)
    monkeypatch.setattr(prefetch.Prefetcher, "_todo", lambda self: [])  # no lookups on the network from tests


@pytest.fixture(autouse=True)
def no_real_band_cloud(monkeypatch, tmp_path):
    """The suite must never see the developer's band-cloud sign-in: it would pull their playlists in and push test ones out."""
    monkeypatch.setattr(cloud, "CRED_FILE", tmp_path / "no-band-cloud.json")
