import json

from prismix import chordify


def _page(**abstract):
    data = {"state": {"player": {"songAbstract": abstract}}}
    return f"<script>window.__INITIAL_DATA__ = {json.dumps(data)};</script>"


def test_parse_reads_key_tempo_and_chord_set():
    html = _page(chordSummary=["D:maj", "E:min", "G:maj", "N"],
                 chordInfo={"derivedKey": "G:maj", "derivedBpm": 130})
    ref = chordify.parse(html)
    assert ref["key"] == {"tonic": 7, "mode": "maj"} and ref["bpm"] == 130
    assert {(c["r"], c["q"]) for c in ref["chords"]} == {(2, "maj"), (4, "min"), (7, "maj")}
    assert chordify.tones(ref) == {2, 6, 9, 4, 7, 11}


def test_parse_gives_none_without_data():
    assert chordify.parse("<html></html>") is None
    assert chordify.parse(_page(chordSummary=[])) is None
    assert chordify.parse("window.__INITIAL_DATA__ = {oops") is None
