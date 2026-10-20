"""Self-check: python3 test_tactician.py (stdlib only, no live meeting needed)."""
import os
import tempfile
import time
from datetime import datetime, timezone
os.environ["TACTICIAN_HOME"] = tempfile.mkdtemp()  # hermetic: never the machine's real tenant folder
import tactician as la
la.USER = "Alex"

SAMPLE = """[11:33:10] Speaker 1: About your PTO. originally you were talking about
[11:33:11] You: Ye ah
[11:33:15] Speaker 1: Uh. taking two weeks off fully
continuation line of the same segment

[11:35:10] Others: well from my perspective: fine
[11:36:00] You:
"""

rows = la.parse_transcript(SAMPLE)
assert [r["speaker"] for r in rows] == ["Speaker 1", "You", "Speaker 1", "Others"], rows
assert rows[2]["text"] == "Uh. taking two weeks off fully continuation line of the same segment"
assert rows[3]["text"] == "well from my perspective: fine"  # colon inside text survives
assert la.parse_transcript(SAMPLE, "11:33:11", "11:33:15") == rows[1:3]

la.POLL_S = 0.05
t = time.time()
batches = list(la.tail_replay(rows, speed=1000))
assert sum(len(b) for b in batches) == 4 and time.time() - t < 2, batches

assert la.format_batch(rows[:1]) == "[11:33:10] Speaker 1: About your PTO. originally you were talking about"
assert la.format_batch([{**rows[0], "backlog": True}]).startswith("[BACKLOG]\n[11:33:10]")

class FakeOut:
    def __init__(self): self.lines = []
    def write(self, s): self.lines.append(s)
    def flush(self): pass
import threading
def fake(ended=False):
    s = la.Session.__new__(la.Session); s.log = FakeOut(); s.ended = ended; s.wrapup = ""
    s.items, s.n_say, s.n_agenda, s.n_commit, s.events, s.items_log, s.cond = {}, 0, 0, 0, [], FakeOut(), threading.Condition()
    s.pending, s.screen, s.look, s.closed = [], None, False, threading.Event()
    s.rec, s.title = {"state": "recording", "since": "", "can_start": False}, "proj call"
    return s
s = fake()
s._note("..."); s._note("  "); assert s.log.lines == [], "silence must be suppressed"
s._note("ANSWER: not in the SOW (SOW.md, Scope)"); assert any("ANSWER" in l for l in s.log.lines)  # plain text still logs
print("ok")

# board replies: say items persist with ids, agenda ranks, done retires, the [BOARD] line tells the model what is open
s = fake()
s._note('```json\n{"say": [{"lines": ["WRONG: you promised it today", "Say: ready for Pat today", ""], "importance": 3, "source": "brief"}],'
        ' "agenda": [{"id": "a1", "text": "Search ready for Pat", "rank": 1}, {"text": "Dana items", "rank": 2}]}\n```')
assert s.items["s1"]["importance"] == 3 and s.items["s1"]["lines"] == ["WRONG: you promised it today", "Say: ready for Pat today"], s.items
assert s.items["a1"]["rank"] == 1 and s.items["a2"]["text"] == "Dana items", s.items
assert "NOTE\nNOTE: [3] s1 WRONG: you promised it today | Say: ready for Pat today (brief)\n" in "".join(s.log.lines), s.log.lines
assert s.board_line() == '[BOARD] say: s1 "WRONG: you promised it today Say:"; agenda: a1 "Search ready for Pat", a2 "Dana items"', s.board_line()
s._note('{"done": ["s1", "nope"], "agenda": [{"id": "a2", "rank": 1}]}')
assert "s1" not in s.items and s.items["a2"]["rank"] == 1 and s.board_line() == '[BOARD] say: none; agenda: a2 "Dana items", a1 "Search ready for Pat"'
assert [e["kind"] for e in s.events] == ["add", "agenda", "agenda", "agenda", "agenda", "done"], [e["kind"] for e in s.events]
assert s.items["a1"]["rank"] == 2, s.items
s._note('{"say": [{"lines": ["old"], "importance": 1}]}'); s.items["s2"]["born"] -= 300; s.expire(); assert "s2" not in s.items
assert la.parse_items("...") is None and la.parse_items("NOTE: x") is None and la.parse_items("[1]") is None
# a raised agenda item stays, struck through: out of the ranking, listed as raised, reopened by a re-rank
s._note('{"done": ["a2"]}')
assert "done" in s.items["a2"] and s.events[-1] == {**s.events[-1], "kind": "done", "id": "a2", "pane": "agenda", "done": s.items["a2"]["done"]}
assert s.board_line() == '[BOARD] say: none; agenda: a1 "Search ready for Pat"; raised: a2 "Dana items"', s.board_line()
assert not s.retire("a2", "done")  # struck once
s._note('{"agenda": [{"text": "PTO plan", "rank": 1}]}')
assert (s.items["a3"]["rank"], s.items["a1"]["rank"]) == (1, 2) and [e["id"] for e in s.events[-2:]] == ["a3", "a1"], s.events[-2:]
s._note('{"agenda": [{"id": "a2", "rank": 1}]}')
assert "done" not in s.items["a2"] and [s.items[i]["rank"] for i in ("a2", "a3", "a1")] == [1, 2, 3], s.items
# The user's direct questions: typed in the console, or typed or clicked on the board
sent = []; s.send = lambda text, label=None, priority=None, image=None: sent.append((text, label) + ((image,) if image else ())) or True
assert s.ask("  is it   in the SOW? ") and sent[-1] == ("[USER] is it in the SOW?", "you asked: is it in the SOW?"), sent
assert s.ask("", "s3", "Quick Search\n backend built") and sent[-1][0] == '[USER] About s3 "Quick Search backend built": more detail, the facts behind it'
assert not s.ask("   ") and len(sent) == 2
s.rec["state"] = "waiting"  # asked before Muesli records: the model is told it has heard nothing
assert s.ask("give me some input") and "not transcribing" in sent[-1][0], sent[-1]
s.rec["state"] = "recording"
# commitments: ids c1.., an id from another pane is not reused, an unchanged text is not re-sent, done withdraws one
s._note('{"commit": [{"text": "Send Sam the extensions doc Fri 10-10"}, {"id": "a1", "text": "Size Dana items"}, {"id": "c1", "text": "Send Sam the extensions doc Fri 10-10"}]}')
assert [(i["id"], i["text"]) for i in s.items.values() if i["pane"] == "commit"] == [("c1", "Send Sam the extensions doc Fri 10-10"), ("c2", "Size Dana items")], s.items
assert s.items["a1"]["pane"] == "agenda" and [e["kind"] for e in s.events].count("commit") == 2
assert s.board_line().endswith('; commitments: c1 "Send Sam the extensions doc Fri 10-10", c2 "Size Dana items"'), s.board_line()
s._note('{"done": ["c2"]}'); assert "c2" not in s.items and s.events[-1]["kind"] == "done"
# The user's verdicts from the board: done retires as the model would, wrong removes and tells the model with the next slice
s._note('{"say": [{"lines": ["Pat gets it Friday"], "importance": 2}]}'); sid = f"s{s.n_say}"
assert s.mark(sid, "wrong") and sid not in s.items and s.events[-1] == {**s.events[-1], "kind": "wrong", "id": sid, "pane": "say", "by": "user"}
assert s.pending == [f'[WRONG] Alex marked {sid} wrong: "Pat gets it Friday"'], s.pending
assert s.mark("a3", "done") and "done" in s.items["a3"] and s.events[-1]["by"] == "user"
assert not s.mark("a3", "maybe") and not s.mark("nope", "wrong") and len(s.pending) == 1
s._note('{"say": [{"lines": ["Cost is 4 cents"], "importance": 1}]}'); s.mark(f"s{s.n_say}", "wrong", " cost is  3.5 cents now ")
assert s.pending.pop() == f'[WRONG] Alex marked s{s.n_say} wrong: "Cost is 4 cents" His reason: cost is 3.5 cents now', s.pending
s.show_transcript = False
assert s.send_transcript([{"timestamp_label": "10:00:00", "speaker": "You", "text": "hi"}])
assert sent[-1][0].startswith(f'[10:00:00] You: hi\n[WRONG] Alex marked {sid} wrong: "Pat gets it Friday"\n[BOARD] ') and s.pending == [], sent[-1]
s._note('{"look": true}'); assert s.look  # the model asks to see the screen
s.screen = ("Meet - abc", b"jpg"); s.send_transcript([{"timestamp_label": "10:00:01", "speaker": "Others", "text": "as you can see"}])
assert "[SCREEN] The meeting window (Meet - abc)" in sent[-1][0] and sent[-1][2] == b"jpg" and s.screen is None, sent[-1]
assert la.changed(None, b"a") and not la.changed(bytes(30), bytes(30)) and la.changed(bytes(30), bytes([200]) * 30)
assert la.CALL_WINDOW.search("Meet - abc-defg-hij") and not la.CALL_WINDOW.search("Google Meet notes - Docs")
# pacing: three say items a reply, loudest first; an answer to the user's question stays up longer than a note
s = fake()
s._note('{"say": [' + ", ".join(f'{{"lines": ["note {i}"], "importance": {1 + (i == 4)}}}' for i in range(5)) + "]}")
assert [i["lines"][0] for i in s.items.values() if i["pane"] == "say"] == ["note 4", "note 0", "note 1"], s.items
s.answering = True
s._note('{"say": [{"lines": ["the answer"], "importance": 2}]}'); ans = f"s{s.n_say}"
s._note('{"say": [{"lines": ["a note"], "importance": 2}]}'); note = f"s{s.n_say}"
assert s.items[ans].get("answer") and not s.items[note].get("answer") and not s.answering
s.items[ans]["born"] -= 300; s.items[note]["born"] -= 300; s.expire()
assert ans in s.items and note not in s.items
s.items[ans]["born"] -= 700; s.expire(); assert ans not in s.items
print("board ok")

# audio: the mic hearing the speakers is dropped, the user's own words are kept, a dead Others track is noticed
row = lambda t, who, text: {"timestamp_label": t, "speaker": who, "text": text}
e = la.Echo()
kept = e.keep([row("08:31:29", "Others", "Yeah, guys, so as per yesterday's call we had hiccups."),
               row("08:32:00", "You", "Yeah, guys, so--"), row("08:32:05", "You", "As per yesterday's call, we..."),
               row("08:32:09", "You", "One of the solutions we discussed was the cache."), row("08:32:10", "You", "Okay.")])
assert [r["text"] for r in kept] == ["Yeah, guys, so as per yesterday's call we had hiccups.",
                                     "One of the solutions we discussed was the cache.", "Okay."], kept
assert e.keep([row("08:35:00", "You", "As per yesterday's call, we...")]), "an Others line over 90 s back is no longer an echo source"
t = la.Tracks()
assert t.check([row("09:00:00", "Others", "here is the architecture"), row("09:02:00", "You", "what about tenants")]) is None
assert t.check([row("09:03:01", "You", "so the data flows into the store")]) == "lost" and t.lost
assert t.check([row("09:04:00", "Others", "Bye.")]) is None and t.lost  # a lone word does not bring it back
assert t.check([row("09:05:00", "Others", "can everyone see my screen")]) == "back" and not t.lost
# a run joining a recording in progress skips the earlier call's transcript on its first poll
polls = iter([([row("08:31:00", "You", "old call")] * 5 + [row("09:02:30", "Others", "new call starts"), row("09:02:31", "You", "hello")] * 3, "recording"),
              ([row("08:40:00", "You", "late row, kept")], "ended")])
def numbered(batch):
    rows, st = batch
    return [{**r, "id": n} for n, r in enumerate(rows, 1)], st
real_poll, la.poll = la.poll, lambda mid, after: numbered(next(polls))
got = [b for b in la.tail_live(63, lambda *_: None, skip_before="09:02:00")]
la.poll = real_poll
assert [r["text"] for r in got[0]] == ["new call starts", "hello"] * 3 and got[0][0].get("backlog") and got[1][0]["text"] == "late row, kept", got
print("audio ok")

# the page server: a snapshot of open items, then the live stream
import urllib.request
s = fake(); s._note('{"say": [{"lines": ["Quick Search backend is built"], "importance": 2, "source": "digest"}]}')
url = la.serve_board(s, 8790); assert url.startswith("http://127.0.0.1:879"), url
assert b"board/index.html" in urllib.request.urlopen(url, timeout=2).read() or la.BOARD_HTML.exists()
r = urllib.request.urlopen(url + "events", timeout=1); first = r.readline().decode()
assert first.startswith("data: ") and '"s1"' in first and '"add"' in first, first
r.readline()  # the blank line that ends the snapshot event
assert '"kind": "rec"' in r.readline().decode(); r.readline()  # the snapshot ends with Muesli's recording state
try:
    extra = r.readline()
except TimeoutError:
    extra = b""
assert not extra.startswith(b"data: "), extra  # the snapshot is not followed by a replay of the history
# POST /ask needs the run's token (only the page has it) and a local Host (no DNS rebinding)
import json, urllib.error
def http(path, body=None, host=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), headers={"Host": host} if host else {})
    try:
        return urllib.request.urlopen(req, timeout=2).status
    except urllib.error.HTTPError as e:
        return e.code
asked = []; s.ask = lambda q, iid="", text="": asked.append((q, iid, text)) or True
page = urllib.request.urlopen(url, timeout=2).read().decode()
assert la.BoardHandler.token in page and "__TACTICIAN_TOKEN__" not in page
assert http("ask", {"token": "nope", "q": "x"}) == 403 and http("ask", {"token": "\u00e9", "q": "x"}) == 403 and http("ask", {}) == 403
assert http("ask", {"token": la.BoardHandler.token, "q": "x"}, host="evil.example:8790") == 403 and http("", host="evil.example") == 403
assert not asked
assert http("ask", {"token": la.BoardHandler.token, "q": "why", "id": "s1", "text": "t"}) == 204 and asked == [("why", "s1", "t")], asked
s.mark = lambda iid, verdict, note="": verdict in ("done", "wrong")
assert http("mark", {"token": la.BoardHandler.token, "id": "s1", "verdict": "wrong"}) == 204
assert http("mark", {"token": "nope", "id": "s1", "verdict": "wrong"}) == 403 and http("other", {"token": la.BoardHandler.token}) == 403
stopped = []; s.stop = lambda: stopped.append(1) or True  # End on the board: token-guarded like the rest
assert http("stop", {"token": "nope"}) == 403 and not stopped and http("stop", {"token": la.BoardHandler.token}) == 204 and stopped
s._note('{"commit": [{"text": "Send the doc"}]}')
r = urllib.request.urlopen(url + "events", timeout=1); snap = [r.readline().decode() for _ in range(6)]
assert any('"kind": "commit"' in l and '"c1"' in l for l in snap), snap  # the snapshot carries commitments too
# Muesli's state: an event only on a change, and in every snapshot; /record only when a shortcut can start it
assert '"kind": "rec"' in snap[4] and '"recording"' in snap[4], snap
n = len(s.events); s.set_rec("recording"); assert len(s.events) == n
s.set_rec("waiting", can_start=False); s.set_rec("waiting"); assert [e["state"] for e in s.events[n:]] == ["waiting"], s.events[n:]
assert http("record", {"token": la.BoardHandler.token}) == 403
recorded = []; la.start_recording = lambda title: recorded.append(title) or True; s.rec["can_start"] = True
assert http("record", {"token": la.BoardHandler.token}) == 204 and recorded == ["proj call"], recorded
assert http("record", {"token": "nope"}) == 403 and len(recorded) == 1
t = fake(ended=True); t.stop_asked = False; la.Session.stop(t); assert not t.stop_asked  # a wrap-up under way is left alone
t = fake(); t.stop_asked = False
try:  # stop() interrupts main() the way Ctrl-C does, once
    la.Session.stop(t); time.sleep(2); raise AssertionError("stop sent no SIGINT")
except KeyboardInterrupt:
    pass
la.Session.stop(t); time.sleep(0.6); assert t.stop_asked
print("server ok")

assert la.load_context(la.Path("/nonexistent/project")) == ("", [], "")
print("context ok")

import tempfile
tmp = la.Path(tempfile.mkdtemp()); (tmp / "proj").mkdir(); (tmp / "p1").mkdir()
la.CONTEXT_DIR = tmp / "contexts"; la.CONTEXT_DIR.mkdir()
(tmp / "p1" / "notes.md").write_text("l1\nl2\nl3\n")
(la.CONTEXT_DIR / "proj.md").write_text(f"add-dir: {tmp}/p1\nbrief: {{project}}/../p1/notes.md:2\nbrief: {tmp}/missing.md\nmap text\n")
assert la.load_context(tmp / "proj") == ("map text", [str(tmp / "p1")], "--- notes.md ---\nl1\nl2"), la.load_context(tmp / "proj")
la.DIGESTS_DIR = tmp / "digests"; la.DIGESTS_DIR.mkdir(); (la.DIGESTS_DIR / "chat.md").write_text("new ask\n" * 130)
assert la.load_context(tmp / "proj")[2].endswith("--- chat.md ---\n" + "new ask\n" * 119 + "new ask")  # newest 120 lines
(la.DIGESTS_DIR / "chat.md").unlink()
seen, files = {}, la.brief_files(tmp / "proj")
assert [(p.resolve(), n) for p, n in files] == [((tmp / "p1" / "notes.md").resolve(), 2), ((tmp / "missing.md").resolve(), None)], files
assert la.brief_news(seen, files) == ""  # the first look only records what the call started with
(tmp / "p1" / "notes.md").write_text("l1\nl1b\nl2\n")
assert la.brief_news(seen, files) == "--- notes.md ---\nl1b" and la.brief_news(seen, files) == ""  # within the line limit
(tmp / "missing.md").write_text("born mid-call\n"); assert la.brief_news(seen, files) == "--- missing.md ---\nborn mid-call"
la.LOOKUP = ""; assert la.lookup_prompt() == (None, "")
la.LOOKUP = str(tmp / "p1" / "notes.md"); assert la.lookup_prompt() == (None, "")  # not executable
os.chmod(la.LOOKUP, 0o755); allow, text = la.lookup_prompt()
assert allow == f"Bash({la.LOOKUP}:*)" and text.startswith("[LOOKUP]"), (allow, text)
la.LOOKUP = ""
(tmp / "p1" / "CLAUDE.md").write_text("digest\n")
assert la.claude_md([str(tmp / "p1"), str(tmp / "proj")]) == f"--- {tmp}/p1/CLAUDE.md ---\ndigest", la.claude_md([str(tmp / "p1")])
la.MEETINGS_DIR = tmp / "meetings"; assert la.last_wrapup(tmp / "proj") == ""
(la.MEETINGS_DIR / "proj").mkdir(parents=True)
(la.MEETINGS_DIR / "proj" / "2026-10-01-1000.md").write_text("old"); (la.MEETINGS_DIR / "proj" / "2026-10-07-1133.md").write_text("new\n")
assert la.last_wrapup(tmp / "proj") == "new"
(la.MEETINGS_DIR / "proj" / "2026-10-07-1133-packet-general.md").write_text("packet")
assert la.last_wrapup(tmp / "proj") == "new"  # packets sit next to the wrap-up and sort after it
rv = tmp / "rv"; la.ROOTS = [rv]; (rv / "lane-a").mkdir(parents=True); (rv / "lane-b").mkdir()
(la.MEETINGS_DIR / "lane-a").mkdir(); (la.MEETINGS_DIR / "lane-a" / "2026-10-08-0948.md").write_text("lane-a call")
(la.MEETINGS_DIR / "rv").mkdir(); (la.MEETINGS_DIR / "rv" / "2026-10-09-1000.md").write_text("general call")
assert la.last_wrapup(rv / "lane-b") == "general call" and la.last_wrapup(rv) == "general call"
assert la.last_wrapup(rv / "lane-b", before="2026-10-09") == "lane-a call"  # one stream across a root's projects
assert la.last_wrapup(tmp / "proj") == "new"  # a project under no root reads only its own
s2 = fake(ended=True); s2.post = []
s2._note('{"say": [{"lines": ["in flight"], "importance": 1}]}'); s2._note("Flagged: x\nCommitments: y"); s2._note("## ACME-SEARCH\n1. do x\n## general\n1. do y")
assert s2.post == ["Flagged: x\nCommitments: y", "## ACME-SEARCH\n1. do x\n## general\n1. do y"] and "s1" in s2.items, s2.post  # items in flight are not the wrap-up
assert la.split_packets(s2.post[1]) == {"acme-search": "1. do x", "general": "1. do y"}, la.split_packets(s2.post[1])
assert la.split_packets("no sections") == {}
# the end: the board's done event carries the wrap-up and the packets, so End shows them instead of closing the window
class FakeP:
    stdin = FakeOut(); stdin.close = lambda: None
    def poll(self): return None
    def wait(self, timeout=None): return 0
s3 = fake(); s3.post = []; s3.p, s3.lock, s3.stderr, s3.out = FakeP(), threading.Lock(), FakeOut(), lambda *_: None
s3.stderr.close = s3.items_log.close = lambda: None
s3.archive, s3.header, s3.turns, s3.cost, s3.cwd, s3.session_id = la.MEETINGS_DIR / "rv" / "2026-10-19-1130.md", "# call", 1, 0.0, tmp, "x"
s3._ask_after = lambda msg, *a: "## general\n1. do y" if msg.startswith("[PACKETS]") else "Flagged: x"
la.time.sleep, sleep = (lambda *_: None), la.time.sleep
s3.finish(); la.time.sleep = sleep
done = s3.events[-1]
assert done["kind"] == "rec" and done["state"] == "done" and done["wrapup"] == "Flagged: x" and done["packets"] == {"general": "1. do y"}, done
print("brief ok")

# replay source: a past run's log (indented transcript lines only; colour codes, notes and status lines dropped)
(tmp / "tactician-proj-20261008-094811.md").write_text(
    "# tactician-proj-20261008-094811\nproject: /x\nstarted: 2026-10-08T09:48:11\n\n\x1b[2m09:48:11 Call date 2026-10-08. Participants: Sam (PM), Pat (dev). Use their names instead of speaker labels.\x1b[0m\n"
    "\x1b[2m  [09:47:13] Others: hello there\x1b[0m\n09:53:15 NOTE\nNOTE: not transcript\n\n  [09:47:18] You: hi\n")
m = la.load_log(tmp / "tactician-proj-20261008-094811.md")
assert m["id"] == "20261008-094811" and m["start_time"] == "2026-10-08 09:48" and m["participants"] == "Sam (PM), Pat (dev)", m
assert [r["text"] for r in la.parse_transcript(m["raw_transcript"])] == ["hello there", "hi"]
assert la.last_wrapup(tmp / "proj", before="2026-10-07") == "old" and la.last_wrapup(tmp / "proj", before="2026-10-01") == ""
assert la.local_time("2026-10-08T13:47:13Z").startswith("2026-10-08 ") and la.local_time("2026-10-08 09:48") == "2026-10-08 09:48"
assert la.local_time("junk") == "junk"
print("replay ok")

# agent sessions: the user's words and the agents' replies from the engagement's folders, noise and other folders left out
import json as _j
la.CLAUDE_PROJECTS = tmp / "projects"; sess = la.CLAUDE_PROJECTS / "-x-rv-lane-a"; sess.mkdir(parents=True)
(la.CLAUDE_PROJECTS / "-x-other").mkdir()
def ent(kind, ts, content, **kw):
    return _j.dumps({"type": kind, "timestamp": ts, "cwd": "/x/rv/lane-a", "message": {"content": content}, **kw}) + "\n"
(sess / "s.jsonl").write_text(
    ent("user", "2026-10-13T17:00:00Z", "scope is approved, Sam confirmed") + "not json\n"
    + ent("assistant", "2026-10-13T17:01:00Z", [{"type": "text", "text": "Noted:   approved."}, {"type": "tool_use", "name": "Read"}])
    + ent("user", "2026-10-13T17:02:00Z", [{"type": "tool_result", "content": "file text"}])
    + ent("user", "2026-10-13T17:03:00Z", "<command-name>/clear</command-name>")
    + ent("assistant", "2026-10-13T17:04:00Z", [{"type": "text", "text": "subagent chatter"}], isSidechain=True)
    + ent("user", "2026-10-13T19:00:00Z", "after the call"))
(la.CLAUDE_PROJECTS / "-x-other" / "o.jsonl").write_text(ent("user", "2026-10-13T17:00:00Z", "another company"))
t0 = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc).timestamp(); t1 = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc).timestamp()
got = la.agent_activity(["/x/rv"], t0, until=t1).splitlines()
assert [g.split("] ", 1)[1] for g in got] == ["Alex: scope is approved, Sam confirmed", "agent: Noted: approved."], got
assert got[0].startswith("[") and " lane-a] " in got[0], got
assert la.agent_activity(["/x/rv"], t0, until=t1, limit=len(got[-1])) == got[-1]  # over the limit: the newest survive
assert la.agent_activity(["/x/rv"], t1).endswith("Alex: after the call")
print("agent sessions ok")
