"""Writes the AltStore / SideStore source for a release: `python packaging/altstore.py VERSION IPA OUT.json`.

The source is a JSON catalogue that AltStore, SideStore and Sideloadly-style stores read; it points at the IPA
attached to the same GitHub release, so adding the source is the whole install flow (the IPA is signed on the
phone with the installer's own free Apple ID).
"""

import datetime as dt
import json
import sys
from pathlib import Path

REPO = "rsx-xxx/prismix"
BUNDLE_ID = "app.prismix.band"


def source(version: str, ipa_size: int, today: dt.date | None = None) -> dict:
    version = version.removeprefix("v")
    day = (today or dt.date.today()).isoformat()
    base = f"https://github.com/{REPO}/releases/download/v{version}"
    return {
        "name": "Prismix",
        "identifier": "app.prismix.source",
        "subtitle": "Play along with the band, from your iPhone or iPad",
        "description": "Prismix for iPhone and iPad: the phone page of your Prismix computer, in a native app.",
        "iconURL": f"https://raw.githubusercontent.com/{REPO}/master/packaging/icon.png",
        "website": f"https://github.com/{REPO}",
        "apps": [
            {
                "name": "Prismix",
                "bundleIdentifier": BUNDLE_ID,
                "developerName": "Prismix",
                "subtitle": "Stems, chords and tabs from your computer",
                "localizedDescription": "Connects to Prismix on your computer over Wi-Fi: follow the band, play along, tune. Works on iPhone and iPad.",
                "iconURL": f"https://raw.githubusercontent.com/{REPO}/master/packaging/icon.png",
                "tintColor": "e8453c",
                "versions": [
                    {
                        "version": version,
                        "buildVersion": str(_build(version)),
                        "date": day,
                        "downloadURL": f"{base}/Prismix.ipa",
                        "localizedDescription": f"https://github.com/{REPO}/releases/tag/v{version}",
                        "size": ipa_size,
                        "minOSVersion": "17.0",
                    }
                ],
            }
        ],
        "news": [],
    }


def _build(version: str) -> int:
    major, minor, patch = (int(p) for p in version.split("."))
    return major * 10000 + minor * 100 + patch


def main(argv: list[str]) -> None:
    version, ipa, out = argv
    Path(out).write_text(json.dumps(source(version, Path(ipa).stat().st_size), indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
