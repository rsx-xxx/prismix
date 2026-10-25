import subprocess

import pytest
from fastapi.testclient import TestClient

from prismix import library, server, uploads
from prismix.config import ffmpeg_exe


@pytest.fixture
def client(tmp_path, monkeypatch):
    for mod in (library, server, uploads):
        monkeypatch.setattr(mod, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(uploads, "_itunes", lambda artist, title: None)
    monkeypatch.setattr(uploads, "find_on_youtube", lambda artist, title, duration: None)
    with TestClient(server.create_app()) as c:
        yield c


def tone(path, *meta):
    args = [ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    for m in meta:
        args += ["-metadata", m]
    subprocess.run([*args, str(path)], check=True)
    return path.read_bytes()


def test_upload_reads_tags_makes_preview_and_dedupes(client, tmp_path):
    data = tone(tmp_path / "t.mp3", "title=Hello", "artist=Someone")
    r = client.post("/api/upload", files={"file": ("01_whatever.mp3", data)})
    assert r.status_code == 200
    info = r.json()
    assert info["title"] == "Hello" and info["channel"] == "Someone"
    assert uploads.is_upload(info["id"]) and info["ext"] == ".mp3"
    assert info["preview"] == "waveform"
    assert (tmp_path / info["id"] / "thumb.jpg").is_file()
    assert client.post("/api/upload", files={"file": ("again.mp3", data)}).json()["id"] == info["id"]
    assert client.get(f"/media/{info['id']}/source.mp3").status_code == 200


def test_upload_title_falls_back_to_file_name(client, tmp_path):
    data = tone(tmp_path / "t.wav")
    info = client.post("/api/upload", files={"file": ("07_my_song.wav", data)}).json()
    assert info["title"] == "My Song" and info["match"] is None


def test_upload_rejects_bad_files(client):
    assert client.post("/api/upload", files={"file": ("a.exe", b"x")}).status_code == 400
    assert client.post("/api/upload", files={"file": ("a.mp3", b"")}).status_code == 400
    assert client.post("/api/upload", files={"file": ("a.mp3", b"not audio at all")}).status_code == 400


def test_uploaded_track_is_processable_without_youtube(client, tmp_path, monkeypatch):
    data = tone(tmp_path / "t.wav")
    vid = client.post("/api/upload", files={"file": ("a.wav", data)}).json()["id"]
    assert library.parse_video_id(vid) == vid
    from prismix import pipeline

    assert pipeline.download(vid, lambda *a: None)["id"] == vid


def test_pretty_name_cleans_file_names():
    assert uploads.pretty_name("03_daft_punk - get_lucky (Official Audio) [320kbps].mp3") == ("Daft Punk", "Get Lucky")
    assert uploads.pretty_name("Bohemian Rhapsody.mp3") == ("", "Bohemian Rhapsody")


def test_pick_match_wants_same_song_and_length():
    cands = [
        {"id": "a" * 11, "title": "Daft Punk - Get Lucky (Live)", "channel": "x", "duration": 369},
        {"id": "b" * 11, "title": "Daft Punk - Get Lucky (Official Audio)", "channel": "Daft Punk", "duration": 248},
        {"id": "c" * 11, "title": "Get Lucky 10 hour loop", "channel": "y", "duration": 36000},
    ]
    assert uploads.pick_match("Daft Punk", "Get Lucky", 247, cands)["id"] == "b" * 11
    assert uploads.pick_match("Daft Punk", "Get Lucky", 100, cands) is None
    assert uploads.pick_match("Daft Punk", "Around the World", 247, cands) is None


def test_upload_adopts_youtube_match_when_untagged(client, tmp_path, monkeypatch):
    hit = {"id": "b" * 11, "title": "Daft Punk - Get Lucky (Official Audio)", "channel": "Daft Punk", "duration": 2}
    monkeypatch.setattr(uploads, "find_on_youtube", lambda artist, title, duration: hit)
    data = tone(tmp_path / "t.wav")
    info = client.post("/api/upload", files={"file": ("get_lucky.wav", data)}).json()
    assert (info["channel"], info["title"]) == ("Daft Punk", "Get Lucky")
    assert info["match"]["id"] == "b" * 11
