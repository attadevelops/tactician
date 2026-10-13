#!/usr/bin/env python3
"""Throwaway test server for board/index.html: replays a recorded run as the live board's event stream.

It answers like caddie.py's BoardHandler: GET / serves board/index.html, GET /events streams
server-sent events, a snapshot of the open items first (seq 0), then the run's events. Every connection
plays its own copy of the run from the start. The run is --run, default the newest run in the tenant's logs folder
(.items.jsonl for the board events, .md for the transcript lines, which the jsonl does not have). Both streams run
on their own clocks, compressed by --speed from their first entry, then are merged by time.

    python3 prototype/replay_events.py                        # http://127.0.0.1:8791/ at 10x
    python3 prototype/replay_events.py --start 20             # join 20 s in: snapshot of what is open, then the rest
    python3 prototype/replay_events.py --start 20 --history   # ... and the past again after the snapshot (BoardHandler's quirk before 2026-10-09)
    python3 prototype/replay_events.py --expire s3,s7         # turn those say items' done events into expire events
"""
import argparse
import http.server
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = Path(os.environ.get("CADDIE_HOME") or Path.home() / ".caddie") / "logs"
RUN = ""  # set from --run in main()
PAGE = ROOT / "board" / "index.html"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
TRANSCRIPT_LINE = re.compile(r"^  \[(\d\d:\d\d:\d\d)\] (You|Others): (.*)$")


def secs(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def now():
    return time.strftime("%H:%M:%S")


def load_timeline(expire_ids):
    """[{at, seq, ...event fields}] sorted by time; `at` is seconds from the stream's first entry, at 1x."""
    items = [json.loads(line) for line in open(RUN + ".items.jsonl", encoding="utf-8") if line.strip()]
    first = secs(items[0]["ts"])
    timeline = []
    for e in items:
        ev = {k: v for k, v in e.items() if k not in ("seq", "ts", "born")}
        if ev["kind"] == "done" and ev["pane"] == "say" and ev["id"] in expire_ids:
            ev["kind"] = "expire"
        timeline.append({"at": secs(e["ts"]) - first, "order": 0, **ev})
    rows, seen = [], set()
    for raw in ANSI.sub("", open(RUN + ".md", encoding="utf-8").read()).splitlines():
        m = TRANSCRIPT_LINE.match(raw)
        if m and m.groups() not in seen:
            seen.add(m.groups())
            rows.append(m.groups())
    rows.sort(key=lambda r: r[0])
    t0 = secs(rows[0][0])
    for t, who, text in rows:
        timeline.append({"at": secs(t) - t0, "order": 1, "kind": "transcript", "t": t, "who": who, "text": text.strip()})
    timeline.sort(key=lambda e: (e["at"], e["order"]))
    for n, e in enumerate(timeline, 1):
        e["seq"] = n
        del e["order"]
    return timeline


def apply(open_items, e, born):
    """What Session.items would hold after event e."""
    if e["kind"] == "add":
        open_items[e["id"]] = {"id": e["id"], "pane": "say", "lines": e["lines"], "source": e["source"],
                               "importance": e["importance"], "born": born}
    elif e["kind"] == "agenda":
        open_items[e["id"]] = {"id": e["id"], "pane": "agenda", "text": e["text"], "rank": e["rank"], "born": born}
    elif e["kind"] in ("done", "expire") and e.get("pane") == "agenda" and e["id"] in open_items:
        open_items[e["id"]]["done"] = born  # raised agenda items stay, struck through (caddie.py since 2026-10-09)
    elif e["kind"] in ("done", "expire"):
        open_items.pop(e["id"], None)


class Handler(http.server.BaseHTTPRequestHandler):
    timeline, speed, start, history = [], 10.0, 0.0, False

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/events":
            body = PAGE.read_bytes() if PAGE.exists() else b"board/index.html missing"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.play()
        except (BrokenPipeError, ConnectionResetError):
            return

    def emit(self, e):
        self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
        self.wfile.flush()

    def keepalive(self):
        self.wfile.write(b"event: ping\ndata: {}\n\n")
        self.wfile.flush()

    def stamped(self, e, born=None):
        out = {"seq": e["seq"], "ts": now(), **{k: v for k, v in e.items() if k not in ("seq", "at")}}
        if e["kind"] in ("add", "agenda"):
            out["born"] = born or time.time()
        return out

    def play(self):
        speed, start = self.speed, self.start
        begin = time.time() - start                       # replay clock: second 0 is the first entry
        past = [e for e in self.timeline if e["at"] / speed < start]
        future = [e for e in self.timeline if e["at"] / speed >= start]
        open_items = {}
        for e in past:
            apply(open_items, e, begin + e["at"] / speed)
        for i in open_items.values():                     # snapshot: what is open now
            self.emit({"seq": 0, "ts": now(), "kind": "add" if i["pane"] == "say" else "agenda", **i})
        if self.history:                                  # the quirk: the whole past again, after the snapshot
            for e in past:
                self.emit(self.stamped(e, begin + e["at"] / speed))
        for e in future:
            due = begin + e["at"] / speed
            while time.time() < due:
                time.sleep(min(15, max(0.0, due - time.time())))
                if time.time() < due:
                    self.keepalive()
            self.emit(self.stamped(e))
        while True:                                       # the run is over; the connection stays open, quiet
            time.sleep(15)
            self.keepalive()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="a run's log path without extension (default: the newest with board events)")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--speed", type=float, default=10.0, help="compression of both clocks (default 10)")
    ap.add_argument("--start", type=float, default=0.0, help="join this many replay seconds in (snapshot first)")
    ap.add_argument("--history", action="store_true", help="after the snapshot, send the past events again")
    ap.add_argument("--expire", default="", help="comma-separated say ids whose done event becomes expire")
    a = ap.parse_args()
    global RUN
    RUN = a.run or str(max(LOGS.glob("*.items.jsonl"), key=lambda f: f.stat().st_mtime)).removesuffix(".items.jsonl")
    Handler.timeline = load_timeline({x for x in a.expire.split(",") if x})
    Handler.speed, Handler.start, Handler.history = a.speed, a.start, a.history
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    kinds = {}
    for e in Handler.timeline:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    last = Handler.timeline[-1]["at"] / a.speed
    print(f"replaying {len(Handler.timeline)} events {kinds} over {last:.0f} s at {a.speed:g}x: http://127.0.0.1:{a.port}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
