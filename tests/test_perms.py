import subprocess

from prismix import perms


def _xattr(monkeypatch, value, code=0):
    monkeypatch.setattr(perms.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=value, stderr=""))


def test_only_what_is_missing_is_listed(monkeypatch):
    monkeypatch.setattr(perms.sys, "platform", "darwin")
    monkeypatch.setattr(perms, "quarantined", lambda app=None: False)
    monkeypatch.setattr(perms, "_mic", lambda: "granted")
    assert perms.state() == []  # nothing left: no buttons
    monkeypatch.setattr(perms, "_mic", lambda: "ask")
    assert [(p["id"], p["how"]) for p in perms.state()] == [("mic", "ask")]  # a system dialog
    monkeypatch.setattr(perms, "_mic", lambda: "denied")
    assert [(p["id"], p["how"]) for p in perms.state()] == [("mic", "settings")]  # only System Settings can change it
    monkeypatch.setattr(perms, "quarantined", lambda app=None: True)
    assert [p["id"] for p in perms.state()] == ["mic", "unlock"]


def test_other_systems_have_nothing_to_allow(monkeypatch):
    monkeypatch.setattr(perms.sys, "platform", "win32")
    assert perms.state() == [] and not perms.grant("mic")


def test_quarantine_is_read_from_the_flags(tmp_path, monkeypatch):
    _xattr(monkeypatch, "0081;5f3e;Safari;UUID")
    assert perms.quarantined(tmp_path)  # downloaded, not yet approved
    _xattr(monkeypatch, "00c1;5f3e;Safari;UUID")
    assert not perms.quarantined(tmp_path)  # approved with Open Anyway
    _xattr(monkeypatch, "", code=1)
    assert not perms.quarantined(tmp_path)  # no mark at all


def test_grant_opens_the_matching_settings_page(monkeypatch):
    monkeypatch.setattr(perms.sys, "platform", "darwin")
    opened = []
    monkeypatch.setattr(perms.subprocess, "Popen", lambda cmd: opened.append(cmd))
    monkeypatch.setattr(perms, "_mic", lambda: "denied")
    assert perms.grant("mic") and opened[-1][1].endswith("Privacy_Microphone")
    assert perms.grant("unlock") and opened[-1][1].endswith("?General")
    assert not perms.grant("nope")
