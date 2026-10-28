"""Bars a tab leaves out although the recording plays them, filled from the tab's own repeated bars.

Many tabs are written for a few bars and then repeat, stop early, skip a section, or leave one instrument resting where
the song plainly goes on. Where the recording's chords for such a bar match a bar the tab does play almost exactly,
that bar's notes are copied in as a template and marked `auto`. Anything weaker stays empty: a guessed tab is worse
than a visible gap. Two kinds of hole are filled:

* a bar where an instrument kind rests between bars where it plays (the bar is mapped to recording harmony);
* recording bars no tab bar was matched to (a skipped part, the tail after the tab ends): new bars are inserted.
"""
from __future__ import annotations

import copy

MIN_TEMPLATE = 0.92  # chord similarity (0..1) of two recording bars for one to stand in for the other
MAX_OPEN = 60


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def fill(parts: dict[str, dict], tmap: list[int | None], rec: list[list[float]]) -> tuple[list[int | None], dict]:
    """Fill `parts` in place; returns (bar map for the new bar list, {copied, inserted, open}).

    `rec` holds one unit chord vector per recording bar (all zero where no chord was heard).
    """
    kinds: dict[str, list[str]] = {}
    for key, p in parts.items():
        if p.get("kind") != "drums":
            kinds.setdefault(p["kind"], []).append(key)
    n = len(tmap)
    stats = {"copied": 0, "inserted": 0, "open": []}
    if not kinds or not n:
        return tmap, stats

    def bar(key: str, i: int) -> dict | None:
        bars = parts[key]["bars"]
        return bars[i] if 0 <= i < len(bars) else None

    def plays(kind: str, i: int) -> bool:
        return any((b := bar(k, i)) and b["n"] for k in kinds[kind])

    def template(r: int, need: list[str], length: float | None, skip: set[int]) -> int | None:
        """The tabbed bar whose recording bar sounds most like recording bar `r` and where every `need` kind plays."""
        best, at = MIN_TEMPLATE, None
        for i in range(n):
            j = tmap[i]
            if j is None or i in skip or not all(plays(k, i) for k in need):
                continue
            if length is not None and any(
                    (b := bar(k, i)) and abs(b["len"] - length) > 1e-3 for kind in need for k in kinds[kind]):
                continue
            s = _cos(rec[r], rec[j])
            if s > best:
                best, at = s, i
        return at

    auto: set[int] = set()
    for order in (range(n), range(n - 1, -1, -1)):  # forward, then backward so holes at either end of a run get a chance
        for i in order:
            j = tmap[i]
            if j is None or not any(rec[j]):
                continue
            for kind, keys in kinds.items():
                if plays(kind, i) or not (plays(kind, i - 1) or plays(kind, i + 1)):
                    continue
                length = next((b["len"] for k in keys if (b := bar(k, i))), None)
                src = template(j, [kind], length, auto)
                if src is None:
                    continue
                for k in keys:
                    s, d = bar(k, src), bar(k, i)
                    if s and d and s["n"]:
                        d["n"], d["auto"] = copy.deepcopy(s["n"]), 1
                auto.add(i)
                stats["copied"] += 1

    mapped = {j for j in tmap if j is not None}
    last = max(mapped, default=-1)
    new: dict[int, int] = {}  # recording bar -> tab bar to copy
    for r in range(len(rec)):
        if r in mapped or not any(rec[r]) or r > last + n:  # a bounded tail, not the whole outro of a long cover
            continue
        src = template(r, list(kinds), None, auto)
        if src is not None:
            new[r] = src
    if new:
        slots: list[tuple[str, int]] = []
        pend = sorted(new)
        for i in range(n):
            j = tmap[i]
            while j is not None and pend and pend[0] < j:
                slots.append(("new", pend.pop(0)))
            slots.append(("old", i))
        slots += [("new", r) for r in pend]
        for p in parts.values():
            old, out = p["bars"], []
            for tag, x in slots:
                if tag == "old":
                    if x < len(old):
                        out.append(old[x])
                elif (s := old[new[x]] if new[x] < len(old) else None):
                    drum = p.get("kind") == "drums"
                    out.append({"len": s["len"], "n": [] if drum else copy.deepcopy(s["n"]), "auto": 1})
            p["bars"] = out
        tmap = [tmap[x] if tag == "old" else x for tag, x in slots]
        stats["inserted"] = len(new)

    done = {i for i in range(len(tmap)) if tmap[i] in new and i >= 0} if new else set()
    left = sorted({
        r for r in range(len(rec)) if any(rec[r]) and r not in {j for j in tmap if j is not None}
    } | {
        tmap[i] for i in range(len(tmap)) if tmap[i] is not None and any(rec[tmap[i]])
        and not any(plays(k, i) for k in kinds) and i not in done
    })
    ranges: list[list[int]] = []
    for r in left:
        if ranges and r == ranges[-1][1] + 1:
            ranges[-1][1] = r
        else:
            ranges.append([r, r])
    stats["open"] = ranges[:MAX_OPEN]
    return tmap, stats
