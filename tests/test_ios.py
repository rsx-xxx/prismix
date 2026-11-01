import datetime as dt
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from prismix.server import create_app

spec = importlib.util.spec_from_file_location("altstore", Path(__file__).parent.parent / "packaging" / "altstore.py")
altstore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(altstore)


def test_altstore_source_points_at_the_release_ipa():
    src = altstore.source("v0.45.0", 1234, dt.date(2026, 11, 1))
    app = src["apps"][0]
    ver = app["versions"][0]
    assert app["bundleIdentifier"] == "app.prismix.band"
    assert ver["version"] == "0.45.0"
    assert ver["buildVersion"] == "4500"
    assert ver["downloadURL"] == "https://github.com/rsx-xxx/prismix/releases/download/v0.45.0/Prismix.ipa"
    assert ver["size"] == 1234 and ver["date"] == "2026-11-01"


def test_getapp_gives_one_landing_link_and_a_qr():
    r = TestClient(create_app()).get("/api/getapp").json()
    assert r["url"].startswith("https://") and r["url"].endswith("/get/")
    assert "qr" in r
