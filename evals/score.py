"""Grade a caddie.py log against an expectations file (meeting-<id>.toml in the tenant's evals folder).

  python3 evals/score.py ~/.caddie/logs/<file>.md --eval ~/.caddie/evals/meeting-<id>.toml [--slack 300]
  python3 evals/score.py --selftest
"""
import argparse
import re
import sys
import tomllib
from pathlib import Path

# ponytail: regex heuristics approximate the manual grades; upgrade path is an LLM judge if they drift
ANSI = re.compile(r"\x1b\[[0-9;]*m")
TRANSCRIPT = re.compile(r"^  \[(\d\d:\d\d:\d\d)\] ")  # meeting time
HEAD = re.compile(r"^(\d\d:\d\d:\d\d) (NOTE|ANSWER|SAYS)$")  # wall-clock time
WALL = re.compile(r"^(\d\d:\d\d:\d\d)(?: (.*))?$")  # any other wall-clock line: tool, status
NOTHING_FOUND = re.compile(
    r"not in (the )?project docs|isn't in (the )?project docs|(aren't|isn't|not) verifiable"
    r"|can't (confirm|verify)|no project source|nothing (on|about) .* in (the )?(docs|repo|project)", re.I)
SOURCE = re.compile(r"\(([^()]*)\)[\s.]*$")  # trailing source parenthetical; "no project source" is caught above
NOT_ROOM_ONLY = (".md", "log", "memory", "web", "/", "§", "section", "line", "git", "doc", "sow", "scope")

SAMPLE = """# sample
  [11:00:00] You: how many sources are live
  [11:00:20] Speaker 1: about two thousand
00:00:05   ⋯ Grep sources
  [11:00:40] You: ok

00:00:08 NOTE
NOTE: Sam says about two thousand. (transcript)

  [11:01:00] You: bye
00:00:15 meeting ended, asking for the wrap-up

00:00:16 ANSWER
ANSWER: 1,602 of 1,690 sources OK on 10-05. (work-log.md)

00:00:20 SAYS
Flagged: none.

Commitments: none.
00:00:21 done: 3 turns, est. $0.010
"""


def secs(hms):
    h, m, s = map(int, hms.split(":"))
    return h * 3600 + m * 60 + s


def find(patterns, text):
    return any(re.search(p, text, re.I) for p in patterns)


def parse(text):
    """Notes in file order. Meeting time = last transcript line above; a block runs to the next log line."""
    notes, mt, cur, asked, ended = [], None, None, None, False
    for line in ANSI.sub("", text).splitlines():
        line = line.rstrip()
        t, h, w = TRANSCRIPT.match(line), HEAD.match(line), WALL.match(line)
        if t or w:
            cur = None
        if t:
            mt = t[1]
        elif h:
            wall = secs(h[1])
            cur = {"label": h[2], "mt": mt, "lines": [], "ended": ended,
                   "asked": asked is not None and (wall - asked) % 86400 <= 60}  # % 86400: logs cross midnight
            notes.append(cur)
        elif w:
            if (w[2] or "").startswith("you asked:"):
                asked = secs(w[1])
            ended = ended or (w[2] or "").startswith("meeting ended")
        elif cur is not None:
            cur["lines"].append(line)  # blank lines inside a block (bullets, paragraphs) stay in it
    for n in notes:
        n["wrap"] = n["label"] == "SAYS"
        n["text"] = " ".join(" ".join(n.pop("lines")).replace("\u2019", "'").split())
    if notes and notes[-1]["ended"]:
        notes[-1]["wrap"] = True  # wrap-up not labelled SAYS; earlier post-marker blocks were still in flight
    return notes


def classify(note, silences):
    if note["wrap"]:
        return "wrapup"
    mt = secs(note["mt"]) if note["mt"] else None
    if mt is not None and not note["asked"] and any(lo <= mt <= hi for lo, hi in silences):
        return "chatter"
    if NOTHING_FOUND.search(note["text"]):
        return "nothing_found"
    src = SOURCE.search(note["text"])
    if src and "transcript" in src[1].lower() and not any(x in src[1].lower() for x in NOT_ROOM_ONLY):
        return "restatement"
    return "useful"


def report(notes, spec, slack):
    silences = [tuple(map(secs, s["window"])) for s in spec.get("silence", [])]
    stale = [p for s in spec.get("stale", []) for p in s["any"]]
    for n in notes:
        n["cls"], n["stale"] = classify(n, silences), find(stale, n["text"])
    out, hits = [], 0
    typed = any(n["asked"] for n in notes)  # a replay from a log carries no [ATTA] questions
    expect = [e for e in spec["expect"] if typed or not e.get("typed")]
    for e in expect:
        lo, hi = map(secs, e["window"])
        hit = next((n for n in notes if n["mt"] and lo <= secs(n["mt"]) <= hi + slack and find(e["any"], n["text"])
                    and (e.get("wrapup") or not n["wrap"])), None)  # the wrap-up only satisfies `wrapup = true` expectations
        hits += hit is not None
        out.append(f"HIT  {e['id']}  {hit['mt']} {hit['text'][:70]}" if hit else f"MISS {e['id']}  ({e['note']})")
    out.append("")
    out += [f"{n['mt'] or '--:--:--'} {n['cls']:<13} {'[STALE] ' if n['stale'] else ''}{n['text'][:80]}" for n in notes]
    n_of = lambda cls: sum(n["cls"] == cls for n in notes)
    out += ["", f"score: recall {hits}/{len(expect)} | notes {len(notes) - n_of('wrapup')} = useful {n_of('useful')}, "
            f"restatement {n_of('restatement')}, nothing-found {n_of('nothing_found')}, chatter {n_of('chatter')} "
            f"| stale {sum(n['stale'] for n in notes)} | wrapup {n_of('wrapup')}"]
    return out


def selftest():
    notes = parse(SAMPLE)
    got = [(n["label"], n["mt"], classify(n, [])) for n in notes]
    # a later batch landed before the NOTE; the in-flight ANSWER after the marker is still a note, the SAYS is the wrap-up
    assert got == [("NOTE", "11:00:40", "restatement"), ("ANSWER", "11:01:00", "useful"), ("SAYS", "11:01:00", "wrapup")], got
    assert notes[2]["text"] == "Flagged: none. Commitments: none.", notes[2]["text"]  # blank line inside a block
    assert classify(notes[0], [(secs("11:00:00"), secs("11:00:50"))]) == "chatter"
    assert classify(parse(SAMPLE.replace("00:00:20 SAYS", "00:00:20 NOTE"))[-1], []) == "wrapup"
    spec = {"expect": [{"id": "t", "window": ["11:00:00", "11:02:00"], "any": ["x"], "note": "", "typed": True},
                       {"id": "u", "window": ["11:00:00", "11:02:00"], "any": ["1,602"], "note": ""},
                       {"id": "w", "window": ["11:00:00", "11:02:00"], "any": ["Flagged"], "note": ""},
                       {"id": "w2", "window": ["11:00:00", "11:02:00"], "any": ["Flagged"], "note": "", "wrapup": True}]}
    assert report(notes, spec, 0)[-1].startswith("score: recall 2/3"), report(notes, spec, 0)[-1]
    print("ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?")
    ap.add_argument("--eval", help="expectations file (required with a log)")
    ap.add_argument("--slack", type=int, default=300, help="seconds of meeting time a note may land after a window closes")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.log or not a.eval:
        ap.error("a log file and --eval are required (or use --selftest)")
    with open(a.eval, "rb") as f:
        spec = tomllib.load(f)
    print("\n".join(report(parse(Path(a.log).read_text(encoding="utf-8")), spec, a.slack)))


if __name__ == "__main__":
    sys.exit(main())
