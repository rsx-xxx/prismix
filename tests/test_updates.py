import httpx

from prismix import updates


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def reset():
    updates._cache.update(at=0.0, ttl=updates.TTL, data=None)


def test_newer_compares_numerically():
    assert updates.newer("v0.0.25", "0.0.24")
    assert updates.newer("v0.0.10", "0.0.9")
    assert not updates.newer("v0.0.24", "0.0.24")
    assert not updates.newer("junk", "0.0.24")


def test_check_picks_platform_asset(monkeypatch):
    reset()
    monkeypatch.setattr(updates, "__version__", "0.0.1")
    monkeypatch.setattr(updates.sys, "platform", "darwin")
    release = {"tag_name": "v9.0.0", "html_url": "https://x/r", "assets": [
        {"name": "Prismix.dmg", "browser_download_url": "https://x/d.dmg"},
        {"name": "Prismix-Setup.exe", "browser_download_url": "https://x/s.exe"}]}
    got = updates.check(client=client(lambda r: httpx.Response(200, json=release)))
    assert got == {"available": True, "latest": "9.0.0", "url": "https://x/r", "download": "https://x/d.dmg"}


def test_check_survives_failure_and_caches(monkeypatch):
    reset()
    calls = []

    def boom(r):
        calls.append(1)
        return httpx.Response(403)

    got = updates.check(client=client(boom))
    assert got["available"] is False
    updates.check(client=client(boom))
    assert len(calls) == 1
