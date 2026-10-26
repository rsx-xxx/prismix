import json

import pytest
from fastapi.testclient import TestClient

from prismix import library, pipeline, server, spotify

SID = "4cOdK2wGLETKBW3PvgPWqT"


def test_parse_links_and_uris():
    assert spotify.parse(f"https://open.spotify.com/track/{SID}?si=abc") == ("track", SID)
    assert spotify.parse(f"open.spotify.com/intl-de/album/{SID}") == ("album", SID)
    assert spotify.parse(f"spotify:playlist:{SID}") == ("playlist", SID)
    assert spotify.parse(f"https://open.spotify.com/embed/artist/{SID}") == ("artist", SID)
    assert spotify.parse("https://open.spotify.com/user/someone") is None
    assert spotify.parse("https://example.com/track/" + SID) is None


def embed(entity):
    return json.dumps({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}})


def test_tracks_from_playlist_and_track(monkeypatch):
    monkeypatch.setattr(spotify, "_page", lambda kind, sid: {
        "title": "Mix", "trackList": [
            {"uri": "spotify:track:" + SID, "title": "Song", "subtitle": "A, B", "duration": 200000, "entityType": "track"},
            {"uri": "spotify:track:x", "title": "", "subtitle": "", "duration": 1}]})
    got = spotify.tracks("playlist", SID)
    assert got["title"] == "Mix"
    assert got["tracks"] == [{"id": SID, "title": "Song", "artist": "A, B", "duration": 200.0}]
    monkeypatch.setattr(spotify, "_page", lambda kind, sid: {
        "title": "Song", "uri": "spotify:track:" + SID, "artists": [{"name": "A"}], "duration": 213573})
    one = spotify.tracks("track", SID)
    assert one["title"] == "A – Song" and one["tracks"][0]["duration"] == pytest.approx(213.573)


def test_empty_collection_is_an_error(monkeypatch):
    monkeypatch.setattr(spotify, "_page", lambda kind, sid: {"title": "x", "trackList": []})
    with pytest.raises(pipeline.PipelineError):
        spotify.tracks("playlist", SID)


def test_score_prefers_studio_cut_with_matching_length():
    want = {"title": "Song", "artist": "Band", "duration": 200}
    studio = {"title": "Band - Song", "channel": "Band - Topic", "duration": 201}
    live = {"title": "Band - Song (Live at X)", "channel": "Band", "duration": 205}
    cover = {"title": "Song cover", "channel": "Someone", "duration": 200}
    assert spotify.score(want, studio) > spotify.score(want, live)
    assert spotify.score(want, studio) > spotify.score(want, cover)
    # a live recording is fine when Spotify's own title says so
    assert spotify.score({**want, "title": "Song (Live)"}, live) > spotify.score({**want, "title": "Song (Live)"}, cover)


def test_resolve_matches_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(spotify, "_cache", {})
    monkeypatch.setattr(spotify, "tracks", lambda k, s: {"title": "Mix", "tracks": [
        {"id": SID, "title": "Song", "artist": "Band", "duration": 200}]})
    calls = []

    def fake_search(q, limit=8, offset=0):
        calls.append(q)
        return [{"id": "aaaaaaaaaaa", "title": "Band - Song", "channel": "Band - Topic", "duration": 200}]

    monkeypatch.setattr(pipeline, "search", fake_search)
    out = spotify.resolve(f"https://open.spotify.com/playlist/{SID}")
    assert out["items"][0]["id"] == "aaaaaaaaaaa" and out["title"] == "Mix"
    spotify.resolve(f"https://open.spotify.com/playlist/{SID}")
    assert len(calls) == 1  # second run comes from the cache


def test_api_accepts_spotify_track_and_rejects_collections(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(spotify, "resolve", lambda url: {"title": "t", "items": [{"id": "dQw4w9WgXcQ"}]})
    c = TestClient(server.create_app())
    r = c.get("/api/playlist", params={"url": f"https://open.spotify.com/album/{SID}"})
    assert r.status_code == 200 and r.json()["items"][0]["id"] == "dQw4w9WgXcQ"
    assert c.post("/api/process", json={"url": f"https://open.spotify.com/album/{SID}"}).status_code == 400
