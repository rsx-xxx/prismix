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
    release = [{"tag_name": "v9.0.0", "html_url": "https://x/r", "assets": [
        {"name": "Prismix.dmg", "browser_download_url": "https://x/d.dmg"},
        {"name": "Prismix-Setup.exe", "browser_download_url": "https://x/s.exe"}]}]
    got = updates.check(client=client(lambda r: httpx.Response(200, json=release)))
    assert got == {"available": True, "latest": "9.0.0", "url": "https://x/r", "download": "https://x/d.dmg"}


def test_check_ignores_the_rolling_latest_release(monkeypatch):
    reset()
    monkeypatch.setattr(updates, "__version__", "0.0.1")
    releases = [{"tag_name": "latest", "assets": []}, {"tag_name": "v0.0.10", "assets": []}, {"tag_name": "v0.0.9", "assets": []}]
    got = updates.check(client=client(lambda r: httpx.Response(200, json=releases)))
    assert got["available"] is True and got["latest"] == "0.0.10"


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


def test_install_refuses_outside_the_packaged_app():
    import pytest

    assert not updates.installable()
    with pytest.raises(RuntimeError):
        updates.install(lambda: None)


def test_install_only_takes_assets_from_own_releases(monkeypatch):
    import pytest

    monkeypatch.setattr(updates, "installable", lambda: True)
    monkeypatch.setattr(updates, "check", lambda force=False: {"available": True, "download": "https://evil.example/Prismix.dmg"})
    with pytest.raises(RuntimeError):
        updates.install(lambda: None)


def test_brew_managed_needs_a_cask_record(tmp_path, monkeypatch):
    brew = tmp_path / "bin" / "brew"
    brew.parent.mkdir()
    brew.write_text("#!/bin/sh\n")
    brew.chmod(0o755)
    monkeypatch.setattr(updates, "_BREW", (str(brew),))
    monkeypatch.setattr(updates.sys, "platform", "darwin")
    assert not updates.brew_managed()
    (tmp_path / "Caskroom" / "prismix" / "0.3.0").mkdir(parents=True)
    assert updates.brew_managed()


def test_brew_upgrade_touches_only_this_cask_and_retries_on_lock(monkeypatch):
    calls = []

    class R:
        def __init__(self, code, err=""):
            self.returncode, self.stdout, self.stderr = code, "", err

    def fake_run(cmd, **kw):
        calls.append(cmd[1:])
        locked = len(calls) == 1
        return R(1, "Error: Cannot lock for download!") if locked else R(0)

    monkeypatch.setattr(updates, "_BREW", ("/bin/sh",))
    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    monkeypatch.setattr(updates.time, "sleep", lambda s: None)
    assert updates._brew_upgrade()
    assert calls == [["update"], ["update"], ["upgrade", "--cask", "prismix", "--greedy"]]


def test_brew_upgrade_gives_up_on_other_errors(monkeypatch):
    class R:
        returncode, stdout, stderr = 1, "", "Error: no such cask"

    monkeypatch.setattr(updates, "_BREW", ("/bin/sh",))
    monkeypatch.setattr(updates.subprocess, "run", lambda cmd, **kw: R())
    assert not updates._brew_upgrade()


def test_restart_refuses_while_busy(monkeypatch):
    monkeypatch.setitem(updates._staged, "path", "/tmp/Prismix.dmg")
    started = []
    monkeypatch.setattr(updates, "_apply", lambda quit_app: started.append(1))
    try:
        updates.restart(lambda: None, lambda: "splitting songs")
    except RuntimeError as e:
        assert "splitting songs" in str(e)
    else:
        raise AssertionError("restart ran while busy")
    assert not started


def test_restart_needs_a_staged_update(monkeypatch):
    monkeypatch.setitem(updates._staged, "path", None)
    try:
        updates.restart(lambda: None)
    except RuntimeError as e:
        assert "ready" in str(e)
    else:
        raise AssertionError("restart ran without a download")
