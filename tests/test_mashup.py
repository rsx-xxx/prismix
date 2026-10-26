import json

from prismix import library, mashup, tabs

VID = "u-dbb316200"


def test_parse():
    assert mashup.parse("Linkin Park - Faint") == ("Linkin Park", "Faint")
    assert mashup.parse("  Faint ") == (None, "Faint")
    assert mashup.parse("Jay-Z") == (None, "Jay-Z")
    assert mashup.parse("") is None


def test_save_sets_clears_and_drops_changed_results(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    d = tmp_path / VID
    d.mkdir()
    (d / "info.json").write_text(json.dumps({"id": VID, "title": "T"}))
    for name in ("lyrics.json", "tabs.json", "tabdata.json"):
        (d / name).write_text("{}")
    assert mashup.save(VID, {"lyrics": "A - B"}) == {"lyrics": "A - B"}
    assert not (d / "lyrics.json").exists() and (d / "tabs.json").exists()
    assert mashup.save(VID, {"lyrics": "A - B", "chords": "C - D"}) == {"lyrics": "A - B", "chords": "C - D"}
    assert not (d / "tabs.json").exists() and not (d / "tabdata.json").exists()
    assert mashup.save(VID, {"lyrics": "", "chords": ""}) == {}
    assert "mashup" not in json.loads((d / "info.json").read_text())


def test_chords_source_drives_the_sheet_search():
    info = {"title": "Салют, Вера (Караоке)", "channel": "x", "mashup": {"chords": "Linkin Park - Faint"}}
    assert tabs.guess_query(info) == [("linkin park", "faint")]


def _mark(ok):
    return {"ok": ok, "name": "C", "r": 0, "q": "maj"}


def test_trusted_drops_sheet_chords_far_from_any_agreement():
    marks = [_mark(True), _mark(False), None, _mark(False), None, None, _mark(False), _mark(False)]
    out = tabs.trusted(marks)
    assert [m is not None for m in out] == [True, True, False, False, False, False, False, False]
