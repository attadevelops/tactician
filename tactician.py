#!/usr/bin/env python3
"""tactician: feed a live Muesli meeting transcript into a Claude Code session for a project and put
what to say on the board (and in the console).

  tactician                           the default project's call (config default_project): wait for Muesli to record, tail,
                                      exit on completion
  tactician <project>                 the same for another project
  tactician <project> --replay 48     replay a past Muesli meeting through the same path
  tactician <project> --replay logs/<run>.md   replay a past run's log (the live chunks, before Muesli's rewrite)
  tactician --probe                   tail Muesli only (no Claude), print per-row lag

<project> is a folder path, or a folder name under a configured project root; run it from any directory.
Configuration and all call data live in the tenant folder, ~/.tactician (or $TACTICIAN_HOME).
Type a line while it runs to ask the assistant something directly, or ask on the board: click an item, or press /.
"""
import argparse
import http.server
import json
import os
import re
import secrets
import shlex
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from datetime import datetime, timedelta
from pathlib import Path

MUESLI_DB = Path.home() / "Library/Application Support/Muesli/muesli.db"
# This machine's tenant: everything about one company's calls lives here, never in the repo (README, "Tenant folder").
HOME = Path(os.environ.get("TACTICIAN_HOME") or Path.home() / ".tactician")
CONFIG = tomllib.loads((HOME / "config.toml").read_text()) if (HOME / "config.toml").is_file() else {}
USER = CONFIG.get("user") or "the user"
ROOTS = [Path(r).expanduser() for r in CONFIG.get("project_roots", [])]  # project folders are found by name under these
LOG_DIR = HOME / "logs"
# PATH usually holds the cmux shim first; the real binary avoids cmux's injected hooks.
CLAUDE_BIN = str(Path.home() / ".local/bin/claude") if (Path.home() / ".local/bin/claude").exists() else shutil.which("claude")

SILENCE = "..."
# ponytail: one poll interval doubles as the batch window; Muesli writes a row about every five seconds, so 5 is the floor.
POLL_S = 5.0
SAY_MAX = 3  # say items kept from one reply
STALL_S = 60  # recording, but no transcript row for this long: the board warns
UPDATE_S = 120  # during a call, how often the agent sessions are read for news
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"  # Claude Code's session transcripts, one folder per working directory
_NOISE = ("<command-", "<local-command", "<system-reminder", "<task-notification", "[Request interrupted",
          "This session is being continued", "Caveat:")
# The user's macOS shortcut running Muesli's "Start Meeting Recording" App Intent, its Title set to the Shortcut Input.
SHORTCUT = os.environ.get("TACTICIAN_SHORTCUT") or CONFIG.get("shortcut") or "Tactician Start Meeting"
STOP_SHORTCUT = CONFIG.get("stop_shortcut") or "Tactician Stop Meeting"  # Muesli's Stop Meeting Recording, run when the call ends
CALL_GONE_S = 45  # the call app has let go of the microphone this long: the call is over
SCREEN_S = 30  # how often the meeting window is looked at; an image goes to the session only when it changed
CALL_WINDOW = re.compile(CONFIG.get("call_windows") or r"^Meet - |Zoom Meeting|Microsoft Teams|Huddle|Webex")  # window titles of a call
# A read-only command over the tenant's source of truth (a second brain's CLI), which the session may run mid-call.
LOOKUP = os.path.expanduser(CONFIG.get("lookup", ""))
_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] ([^:\]]+): ?(.*)$")

PROMPT = """You are Tactician, {user}'s live meeting prompter. You run headless for the call happening right now. {user} is in the call and reads a board on a side monitor: a Say pane (the words to say now) and an Agenda pane (what to raise, ranked). They have no time to read explanations: give them the words, in plain short lines, and decide how loud they need to be.

Each user message is a new slice of the live speech-to-text transcript, lines like "[HH:MM:SS] Speaker: text". "You" is {user}. The live transcript labels everyone else "Others" (speaker names only arrive after the call), so name a person only when the content makes it certain (whoever runs the call and hands out action items; the front-end developer answering front-end questions); otherwise say "the room". A "You" line that repeats what "Others" said moments before is {user}'s microphone hearing the speakers: it is the room talking, not {user}, and never a commitment of theirs. "[AUDIO]" says when the other participants' track has stopped arriving, so that every voice reaches you as "You", and when it is back. Expect garbled words, missing punctuation and phonetic spellings of project terms. A message starting with "[CONTEXT]" names the call date and the participants. "[BACKLOG]" is transcript from before you attached: read it and reply "..." unless something is urgent. "[USER]" is {user} asking you directly, typed, or by clicking a board item ("About s3 ..."): always answer, as a say item, with what the board did not already say; when they state a fact instead of asking, it is settled like an [UPDATE]: change the board, no say item. "[UPDATE]" is what {user} and their own agent sessions (the coding assistants working in their project folders) said since your last look, newest last; it is newer than the brief and the digest. A decision, approval or status in it is settled: retire the agenda and say items it settles, never raise them again, and use its facts from then on. The brief's "recent agent sessions" block is the same record from before the call. A "[BOARD]" line at the end of a slice lists what is on their board right now, with ids; "raised" are agenda items already done, which stay on their board struck through. "[WRONG]" is {user} marking a board item wrong or useless; it is already gone from their board: never repeat that point or any fact in it. "[SCREEN]" comes with an image of the meeting window: what someone is sharing (slides, a document, a page, a mockup). Use what it shows as you use the transcript; a say item that rests on it has "screen" as its source. When the room points at something on screen ("as you can see", "this page", "here") and you have no image of it yet, add "look": true to your reply and the next slice brings one. "[MEETING ENDED]" asks for the wrap-up.

Reply to each slice with exactly "..." (three dots, nothing else) or with one JSON object, no prose before or after it and no code fences:
{"say": [{"lines": ["...", "..."], "importance": 3, "source": "contract.md, Scope"}], "agenda": [{"id": "a1", "text": "...", "rank": 1}], "commit": [{"id": "c1", "text": "..."}], "done": ["s2", "a3"]}
Every key is optional. "say" adds items to the Say pane, at most three per reply (an answer to {user} too: the best three, not every angle): 1 to 3 lines, at most 12 words a line, words {user} can read aloud; the source goes in "source" (file and section, or "digest", "brief", "memory", "web", "general knowledge"), never in the lines. Register: the lines are words {user} will say, so fit them to whoever they answer. By default the listener is not technical (the client, the project manager, an executive): plain words about what users will see, dates, effort, cost and decisions. Keep every number that matters, in plain units ("about four cents a question, under the five-cent target"); drop only the labels the listener would not know (ticket or question ids, codes, field, file or service names, metric names like p50); when the exact term matters, add it once in brackets after the plain words ("answers in about five seconds (p50 4.6 s)"). Go technical only when a developer or DevOps person asked, or the line answers one: the participants and the people in the digest say who is who, and in the live transcript technical vocabulary in the question is the tell. An executive gets the outcome first, in one line. When unsure who is asking, stay plain. Agenda text and "source" are for {user}'s eyes and can stay terse. importance 3: {user} just said, or is about to say, something wrong, or is about to commit to a date or a number that conflicts with what they promised or with the SOW; nothing else is a 3. The first line starts with "WRONG:" or "CAREFUL:" and the next line gives the words to say instead. importance 2: a fact that changes what they say next (the answer to a question asked of them, a status, a number, a decision already made). importance 1: useful background. "agenda" adds or re-ranks Agenda items (ids a1, a2, ...; reuse an id to re-rank; text at most 10 words). "commit" records every commitment {user} makes on this call, firm or loose ("I'll send it Friday", "I'm looking into it"), as the transcript shows them saying it; never one from the brief, the digest or an earlier call: ids c1, c2, ..., at most 10 words, with the date when one was said; reuse an id to correct one. Commitments stay on their board for a read-back before the call ends. "done" retires items by id once {user} has said them, the question is answered, or the topic has moved on. "Not known yet" is an answer: when the room says it will be settled later ("we'll know tomorrow", "TBD", "I'll find out"), retire every item that asks it, keep that date, and never ask it again on this call. Retire instead of re-posting, and never add an item that is already on the board; an item that makes an open item's point from another angle is a repeat, so do not post it. State a commitment once, with the date the brief records, and never restate it with another date.

Speak only when the project context has something the room does not: a fact, a correction, a number, a date, a decision already made, or the exact words that put {user}'s work in its true and best light (when something is nearly done, the words are "built and tested, two checks left", not the list of what is missing). When {user} says done, built or finished, check the plan's open items for that feature and give them the honest one-liner. Silence without checking is only for small talk, scheduling and logistics; for any claim or question about scope, status, dates, numbers or design, look first, then speak or stay silent. Check every plan, sprint, deadline or "next week" against the dates in your context (the SOW end, holidays, last call's commitments). Say a boundary at most twice a call: once when it first matters and once as a last-chance reminder before the call ends; in between, raise it only when {user} agrees to something that lands past it, at importance 3. Someone else naming a date past it is not a reason to repeat it. When {user} states a commitment that differs from what they promised last call, say so at importance 3. When the conversation moves to another lane (the context names them), re-rank the agenda so that lane's open items are on top and retire what has been raised. Never guess or fabricate a project fact (a status, date, number, owner or decision); when the context does not settle one, stay silent unless {user} asked, then say "Not in project docs".

When someone else is presenting or explaining (a demo, an architecture walkthrough, a knowledge share) and {user} is mostly listening, the project context often has nothing on the topic. Then speak from general knowledge, at importance 1, with "general knowledge" as the source, in two ways. First, the first time the presenters lean on a term {user} may not know (an acronym, a product, a protocol), one plain line on what it is and why it matters here ("CDC: every database change becomes an event others can listen to"). Second, a sharp question {user} could ask about what is shown, aimed at where it meets their product (its tenants, integrations, cost, risk, timeline or who owns the work), at most two per topic; check the transcript first and never ask what the presenters have already answered or are answering now. Never present general knowledge as a project fact, never use it for a status, date or commitment, and say nothing during small talk or logistics.

Speed beats completeness: the right three words in ten seconds beat a paragraph in a minute. Project root: {root}. Lookup order: (1) `graphify query "<2-4 key terms>" --graph {root}/graphify-out/graph.json` when that file exists, then Read the best hit; (2) Grep across {root}/docs, {root}/_internal and the sibling folders you can read; (3) the digest and memory already in your context. At most three tool calls per slice, and only to look something up: never call a tool to wait or to do nothing (no `true`, `echo` or `sleep`); when there is nothing to post yet, reply "..." and the next slice arrives in seconds. You are read-only: never modify files or run anything that changes state.

Reply to the [CONTEXT] message with the initial agenda: five to eight items, ranked, from the brief's call queue (items the project sessions want raised), its open commitments and anything due today. On "[MEETING ENDED]" reply in plain text, not JSON: a wrap-up of at most 8 lines, no bullets: what you flagged, commitments {user} made, which call-queue items were raised, open questions, and what to check after the call. Then, for each thing {user} said on the call that the project context contradicts (a figure, date, status, scope, owner or term), one more line: "You said: <what they said, with time>. In fact: <the right fact>. Source: <document>." so they can set it straight with the room. Only a claim the transcript states clearly: never from a garbled or half-heard line, never one {user} corrected in their next lines, and none when they said nothing wrong. Name who owns an action only when the transcript makes it certain; otherwise write "the room"."""

PACKETS_MSG = """[PACKETS] The call is over. For each project lane this call touched, write an action packet for the session that works in that lane. Plain text, no JSON. One section per lane, each starting with a line "## <lane>" (the repo or lane, as the context names them; "general" for the rest). In each section: Decisions made on the call. Action items, numbered, each on one line: "N. [who] <the action, at most 12 words>. Why: <its source document and the transcript evidence with time>", where who is agent (this lane's session can do it: look up, build, write a document, prepare), message (a message {user} sends; the session drafts it), you ({user} must do it in person) or others (someone else owns it; record only). Open questions and who answers them. Gaps in what {user} said that the artefacts should fix. Dates that bind. Write for an agent that will triage, gather the documents and propose, and must stop before anything client-facing or any commitment. Nothing for lanes the call did not touch."""


def split_packets(text):
    """'## lane' sections of the packets reply -> {slug: body}."""
    out, lane = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            lane = re.sub(r"[^a-z0-9]+", "-", line[3:].strip().lower()).strip("-")
            out[lane] = ""
        elif lane:
            out[lane] += line + "\n"
    return {k: v.strip() for k, v in out.items() if v.strip()}


# Deny rules override every allow rule from any settings source (a project's
# settings auto-allow aws/terraform read commands; local settings are not loaded at all).
DENY_SETTINGS = {"permissions": {"deny": [
    "Edit", "Write", "NotebookEdit", "Agent",
    "Bash(aws:*)", "Bash(terraform:*)", "Bash(docker:*)", "Bash(git push:*)", "Bash(git commit:*)",
    "Bash(git checkout:*)", "Bash(git reset:*)", "Bash(rm:*)", "Bash(mv:*)",
    "Bash(python:*)", "Bash(python3:*)", "Bash(pytest:*)", "Bash(pip:*)", "Bash(npm:*)", "Bash(pnpm:*)",
]}}
ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Skill", "WebSearch", "WebFetch", "Bash(graphify:*)", "Bash(rg:*)"]  # cwd is not the repo, so no git
TOOLS = ["Read", "Grep", "Glob", "Skill", "Bash", "WebSearch", "WebFetch"]

CONTEXT_DIR = HOME / "contexts"
MEETINGS_DIR = HOME / "meetings"  # archived wrap-ups, one file per live meeting
DIGESTS_DIR = HOME / "digests"  # the channel readers' rolling digests, one per channel (skills/tactician-read), newest first


def _context_lines(project):
    for name in (project.parent.name, project.name):
        f = CONTEXT_DIR / f"{name}.md"
        if f.exists():
            yield from f.read_text().splitlines()


def _brief_spec(line, project):
    """A brief: line's file and line limit (None for all)."""
    spec = line.split(":", 1)[1].strip().replace("{project}", str(project))
    path, limit = spec.rsplit(":", 1) if re.search(r":\d+$", spec) else (spec, "")
    return Path(os.path.expanduser(path)), int(limit) if limit else None


def brief_files(project):
    """The context map's brief: files with their line limits, including ones that do not exist yet."""
    return [_brief_spec(l, project) for l in _context_lines(project) if l.startswith("brief:")]


def brief_news(seen, files):
    """Lines new in the brief files since the last look, as one block or ''. `seen` (file -> its lines) is updated in
    place; a file's first look only records it, so the brief the call started with is never sent twice."""
    out = ""
    for p, limit in files:
        try:
            lines = p.read_text().splitlines()[:limit]
        except OSError:
            seen.setdefault(p, set())  # a file that appears later is all news
            continue
        new = [l for l in lines if l.strip() and l not in seen.get(p, set())]
        if p in seen and new:
            out += f"--- {p.name} ---\n" + "\n".join(new) + "\n"
        seen[p] = set(lines)
    return out.strip()


def lookup_prompt():
    """The allow rule and prompt text for the config's lookup command, or (None, "") when it is unset or not runnable."""
    if not LOOKUP or not os.access(LOOKUP, os.X_OK):
        return None, ""
    return f"Bash({LOOKUP}:*)", (
        f"[LOOKUP] `{LOOKUP}` is a read-only command over {USER}'s source of truth: what each project's status is now, "
        "blockers, who is waiting on whom, decisions, what is deployed in each environment, people. It is newer than the "
        f"brief and the digest. When a question turns on current status, a deployment or a person, run it (`{LOOKUP} --help` "
        "once for its commands) before answering, at most two calls; its answer outranks older notes. Start the command with "
        "that exact full path (no cd, no relative path, or it is refused); piping into grep or head is fine.")


def load_context(project):
    """contexts/<parent-dir>.md or contexts/<project>.md: add-dir: lines grant read access, brief: lines name files
    (path[:max-lines], {project} allowed) sent as the pre-meeting brief, every other line is prompt text."""
    text, dirs, brief = "", [], ""
    for line in _context_lines(project):
        if line.startswith("add-dir:"):
            pattern = os.path.expanduser(line.split(":", 1)[1].strip())
            dirs += [d for d in sorted(__import__("glob").glob(pattern)) if os.path.isdir(d)]
        elif line.startswith("brief:"):
            p, limit = _brief_spec(line, project)
            if p.is_file():
                brief += f"\n--- {p.name} ---\n" + "\n".join(p.read_text().splitlines()[:limit]) + "\n"
        else:
            text += line + "\n"
    for p in sorted(DIGESTS_DIR.glob("*.md")):  # ponytail: every channel in every brief; per-engagement filter if a Mac has several
        brief += f"\n--- {p.name} ---\n" + "\n".join(p.read_text().splitlines()[:120]) + "\n"
    return text.strip(), dirs, brief.strip()


def last_wrapup(project, before=None):
    """Newest archived wrap-up for this project (meetings/<project>/<date>.md, not its packets), or ''. The calls of one
    engagement are one stream whatever folder a call was started from, so a project under a configured root reads the
    newest across that root's projects (and the root itself, for a general call).
    `before` (YYYY-MM-DD) keeps only earlier calls' wrap-ups, so a replay never reads its own outcome."""
    names = {project.name}
    for root in (r for r in ROOTS if project == r or r in project.parents):
        names |= {d.name for d in MEETINGS_DIR.glob("*") if d.name == root.name or (root / d.name).is_dir()}
    files = sorted((f for n in names for f in (MEETINGS_DIR / n).glob("*.md")
                    if "-packet-" not in f.name and (not before or f.name[:10] < before)), key=lambda f: f.name)
    return files[-1].read_text().strip() if files else ""


def agent_activity(dirs, since, until=None, limit=12000, each=700):
    """What the user and their own Claude Code sessions in these folders said between since and until (epoch seconds), oldest
    first: their words (pasted chat included) and the agents' replies, with tool traffic and harness noise left out, the
    newest kept when it runs over `limit` characters. Tactician's own sessions run under /private/tmp and never match."""
    keys = tuple("-" + re.sub(r"[^A-Za-z0-9]", "-", str(d).strip("/")) for d in dirs)  # Claude Code's folder naming
    found = []
    for p in CLAUDE_PROJECTS.glob("*"):
        if not p.name.startswith(keys):
            continue
        # ponytail: reads each transcript written since `since` whole (a few MB); keep byte offsets if a poll gets slow
        for f in p.glob("*.jsonl"):
            if f.stat().st_mtime < since:
                continue
            for line in f.open(errors="replace"):
                try:
                    e = json.loads(line)
                    t = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                if e.get("type") not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta") \
                        or t < since or (until and t >= until):
                    continue
                body = (e.get("message") or {}).get("content")
                text = body if isinstance(body, str) else " ".join(
                    b.get("text", "") for b in body or [] if isinstance(b, dict) and b.get("type") == "text")
                text = " ".join(text.split())
                if text and not text.startswith(_NOISE):
                    who = USER if e["type"] == "user" else "agent"
                    found.append((t, f"[{datetime.fromtimestamp(t):%H:%M} {Path(e.get('cwd') or p.name).name}] {who}: {text[:each]}"))
    lines = [line for _, line in sorted(found)]
    while lines and sum(map(len, lines)) > limit:
        lines.pop(0)
    return "\n".join(lines)


def claude_md(dirs):
    """The CLAUDE.md of each readable folder (the engagement digest). Tactician puts it in the prompt itself, so a run can save
    exactly what it saw and a replay can load that copy instead of today's file, which may already know how the call ended."""
    return "\n\n".join(f"--- {d}/CLAUDE.md ---\n{(Path(d) / 'CLAUDE.md').read_text().strip()}"
                       for d in dirs if (Path(d) / "CLAUDE.md").is_file())


COLOR = sys.stdout.isatty()


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if COLOR else s


def now():
    return datetime.now().strftime("%H:%M:%S")


def local_time(iso):
    """Muesli stores UTC ('2026-10-08T13:47:13Z'); the log, the archive and the call date use local time."""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(iso)


# ---------------------------------------------------------------- Muesli (read-only)
def db():
    if not MUESLI_DB.exists():
        sys.exit(f"Muesli database not found: {MUESLI_DB}")
    con = sqlite3.connect(f"file:{MUESLI_DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def active_meeting():
    with db() as con:
        return con.execute(
            "SELECT id, title, start_time FROM meetings WHERE meeting_status='recording' "
            "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()


def poll(mid, after_id):
    """New checkpoint rows after the cursor, plus the meeting's current status."""
    with db() as con:
        rows = con.execute(
            "SELECT id, timestamp_label, speaker, text FROM meeting_transcript_checkpoints "
            "WHERE meeting_id=? AND id>? ORDER BY id", (mid, after_id)).fetchall()
        st = con.execute("SELECT meeting_status FROM meetings WHERE id=?", (mid,)).fetchone()
    return [dict(r) for r in rows], (st["meeting_status"] if st else "missing")


def has_shortcut(name=None):
    try:
        return (name or SHORTCUT) in subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=15).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return False


def stop_recording():
    """End Muesli's recording through the stop shortcut (Muesli's Stop Meeting Recording); True when it ran."""
    try:
        return subprocess.run(["shortcuts", "run", STOP_SHORTCUT], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def mic_users():
    """Bundle ids (or "pid N") of the processes with the microphone open, from CoreAudio, Muesli's own call signal.
    A call app (browser, Zoom, Teams) keeps it open while muted, and lets go when the call ends."""
    import ctypes, ctypes.util
    ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]

    class Addr(ctypes.Structure):
        _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]

    def prop(obj, sel, size):
        a = Addr(int.from_bytes(sel.encode(), "big"), int.from_bytes(b"glob", "big"), 0)
        n, buf = ctypes.c_uint32(size), ctypes.create_string_buffer(size)
        return buf.raw[:n.value] if ca.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(n), buf) == 0 else b""

    out, raw = [], prop(1, "prs#", 4 * 1024)  # every process CoreAudio knows
    for obj in (int.from_bytes(raw[i:i + 4], "little") for i in range(0, len(raw), 4)):
        if prop(obj, "piri", 4) != b"\x01\x00\x00\x00":  # is running input
            continue
        ref, name = int.from_bytes(prop(obj, "pbid", 8) or bytes(8), "little"), ctypes.create_string_buffer(256)
        ok = ref and cf.CFStringGetCString(ref, name, 256, 0x08000100) and name.value
        out.append(name.value.decode() if ok else f"pid {int.from_bytes(prop(obj, 'ppid', 4) or bytes(4), 'little')}")
    return out


def start_recording(title):
    """Start a Muesli recording titled `title` through the user's shortcut; True when the shortcut ran."""
    f = LOG_DIR / ".recording-title.txt"
    f.write_text(title)
    try:
        r = subprocess.run(["shortcuts", "run", SHORTCUT, "--input-path", str(f)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def make_shortcut():
    """Write the two shortcuts (Muesli's Start Meeting Recording with Title = Shortcut Input, and Stop Meeting Recording),
    sign them and open the imports."""
    title = {"Value": {"string": "￼", "attachmentsByRange": {"{0, 1}": {"Type": "ExtensionInput"}}},
             "WFSerializationType": "WFTextTokenString"}
    for name, intent, params in ((SHORTCUT, "StartMeetingIntent", {"meetingTitle": title}), (STOP_SHORTCUT, "StopMeetingIntent", {})):
        if has_shortcut(name):
            print(f"The '{name}' shortcut exists.")
            continue
        action = {"WFWorkflowActionIdentifier": f"com.muesli.app.{intent}",
                  "WFWorkflowActionParameters": {
                      "AppIntentDescriptor": {"AppIntentIdentifier": intent, "BundleIdentifier": "com.muesli.app",
                                              "Name": "Muesli", "TeamIdentifier": "58W55QJ567"}, **params}}
        write_shortcut(name, action)
    return 0


def write_shortcut(name, action):
    import plistlib
    shortcut = {"WFWorkflowActions": [action], "WFWorkflowClientVersion": "3000",
                "WFWorkflowMinimumClientVersion": 900, "WFWorkflowMinimumClientVersionString": "900",
                "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": 59511, "WFWorkflowIconStartColor": 4282601983},
                "WFWorkflowHasShortcutInputVariables": True,
                "WFWorkflowInputContentItemClasses": ["WFStringContentItem", "WFGenericFileContentItem", "WFRichTextContentItem"],
                "WFWorkflowOutputContentItemClasses": [], "WFWorkflowImportQuestions": [], "WFWorkflowTypes": [],
                "WFQuickActionSurfaces": []}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    raw, signed = LOG_DIR / ".unsigned.shortcut", LOG_DIR / f"{name}.shortcut"  # sign reads only a .shortcut name
    raw.write_bytes(plistlib.dumps(shortcut, fmt=plistlib.FMT_BINARY))
    r = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "-i", str(raw), "-o", str(signed)], capture_output=True, text=True)
    if r.returncode:  # signing needs iCloud; company-managed Macs often have none
        sys.exit(f"Could not sign '{name}' ({(r.stderr or r.stdout).strip()}): without iCloud, build it in the Shortcuts app "
                 "(claude /tactician-setup does it)")
    subprocess.run(["open", str(signed)], check=True)
    print(f"In Shortcuts: Add Shortcut ('{name}'). On its first run choose Always Allow, so it can reach Muesli.")


def call_window():
    """(window id, title) of the on-screen meeting window, from CoreGraphics' window list; (None, reason) when there is none.
    Titles are hidden without Screen Recording permission, so no titles at all means no permission."""
    import ctypes, ctypes.util
    V = ctypes.c_void_p
    cg, cf = ctypes.CDLL(ctypes.util.find_library("CoreGraphics")), ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    cg.CGWindowListCopyWindowInfo.restype, cg.CGWindowListCopyWindowInfo.argtypes = V, [ctypes.c_uint32, ctypes.c_uint32]
    cf.CFArrayGetCount.restype, cf.CFArrayGetCount.argtypes = ctypes.c_long, [V]
    cf.CFArrayGetValueAtIndex.restype, cf.CFArrayGetValueAtIndex.argtypes = V, [V, ctypes.c_long]
    cf.CFDictionaryGetValue.restype, cf.CFDictionaryGetValue.argtypes = V, [V, V]
    cf.CFStringCreateWithCString.restype, cf.CFStringCreateWithCString.argtypes = V, [V, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.argtypes = [V, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFNumberGetValue.argtypes, cf.CFRelease.argtypes = [V, ctypes.c_int, V], [V]
    key = lambda k: cf.CFStringCreateWithCString(None, k.encode(), 0x08000100)
    k_num, k_owner, k_name = key("kCGWindowNumber"), key("kCGWindowOwnerName"), key("kCGWindowName")

    def text(ref, b=ctypes.create_string_buffer(512)):
        return b.value.decode() if ref and cf.CFStringGetCString(ref, b, 512, 0x08000100) else ""

    arr, titled = cg.CGWindowListCopyWindowInfo(1 | 16, 0), False  # on screen, no desktop elements; front to back
    try:
        for i in range(cf.CFArrayGetCount(arr)):
            d = cf.CFArrayGetValueAtIndex(arr, i)
            owner, title = text(cf.CFDictionaryGetValue(d, k_owner)), text(cf.CFDictionaryGetValue(d, k_name))
            titled |= bool(title) and owner not in ("Window Server", "Control Center", "Dock")
            if title and CALL_WINDOW.search(title):
                n = ctypes.c_int64()
                cf.CFNumberGetValue(cf.CFDictionaryGetValue(d, k_num), 4, ctypes.byref(n))
                return n.value, title
    finally:
        cf.CFRelease(arr)
    return None, "no meeting window on screen" if titled else "no Screen Recording permission"


def capture_window(wid, path):
    """The window as a JPEG at most 1568 px on its long side (the size the model reads best); a tiny bitmap of it, the
    fingerprint for 'did it change'. None when the capture failed."""
    small = path.with_suffix(".bmp")
    try:
        if subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), "-t", "jpg", str(path)], capture_output=True, timeout=15).returncode:
            return None
        subprocess.run(["sips", "-Z", "1568", str(path)], capture_output=True, timeout=15)
        subprocess.run(["sips", "-z", "18", "32", "-s", "format", "bmp", str(path), "--out", str(small)], capture_output=True, timeout=15)
        return small.read_bytes()[-18 * 32 * 3:]  # ponytail: raw tail of the bitmap is its pixels, enough to compare
    except (OSError, subprocess.TimeoutExpired):
        return None


def changed(a, b, threshold=8):
    """Mean per-byte difference of two fingerprints above threshold: the shared screen moved on (a new slide, a scroll)."""
    return a is None or b is None or len(a) != len(b) or sum(abs(x - y) for x, y in zip(a, b)) / len(a) > threshold


def wait_for_recording(report):
    last = 0
    while True:
        m = active_meeting()
        if m:
            return m
        if time.time() - last > 30:
            report(c("2", f"{now()} waiting for Muesli to start recording..."))
            last = time.time()
        time.sleep(POLL_S)


def tail_live(mid, report, skip_before=None):
    """Yield the new rows every POLL_S (an empty list on a quiet poll) until the recording ends. skip_before (HH:MM:SS):
    the first poll drops older rows, which belong to an earlier call on the same recording."""
    cursor, first = 0, True
    while True:
        rows, status = poll(mid, cursor)
        if rows:
            cursor = rows[-1]["id"]
            if first and skip_before:
                rows = [r for r in rows if r["timestamp_label"] >= skip_before]
        if rows:
            if first and len(rows) > 3:
                rows[0]["backlog"] = True
            first = False
        yield rows
        if status != "recording":
            report(c("2", f"{now()} Muesli marked meeting {mid} '{status}'"))
            return
        time.sleep(POLL_S)


# ---------------------------------------------------------------- replay
def _secs(label):
    h, m, s = label.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_transcript(raw, t_from=None, t_to=None):
    rows = []
    for line in (raw or "").splitlines():
        m = _LINE.match(line)
        if m:
            rows.append({"timestamp_label": m[1], "speaker": m[2].strip(), "text": m[3].strip()})
        elif rows and line.strip():  # continuation of a multi-line segment
            rows[-1]["text"] += " " + line.strip()
    return [r for r in rows if r["text"]
            and (t_from is None or r["timestamp_label"] >= t_from)
            and (t_to is None or r["timestamp_label"] <= t_to)]


def load_transcript(mid):
    """The meeting row (start_time, raw_transcript) of a completed Muesli meeting."""
    with db() as con:
        r = con.execute("SELECT id, start_time, raw_transcript FROM meetings WHERE id=?", (mid,)).fetchone()
    if not r or not r["raw_transcript"]:
        sys.exit(f"Muesli meeting {mid} has no transcript")
    return r


_LOG_LINE = re.compile(r"^  (\[\d\d:\d\d:\d\d\] .*)$")  # a run's log indents transcript lines by two spaces


def load_log(path):
    """A past run's log as a replay source: the transcript lines exactly as that run saw them. Muesli writes
    raw_transcript only at completion (hours later on 2026-10-08), re-chunked and diarized; the log is the faithful copy."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", Path(path).read_text())
    lines = [m[1] for m in map(_LOG_LINE.match, text.splitlines()) if m]
    if not lines:
        sys.exit(f"no transcript lines in {path}")
    started = re.search(r"^started: (\d{4}-\d\d-\d\d)T(\d\d:\d\d)", text, re.M)
    who = re.search(r"Call date [\d-]+\. Participants: (.*?)\. Use their names", text)  # who that run was told was on the call
    return {"id": "-".join(Path(path).stem.split("-")[-2:]), "start_time": f"{started[1]} {started[2]}" if started else "",
            "raw_transcript": "\n".join(lines), "participants": who[1] if who else None}


def tail_replay(rows, speed):
    """Pace rows by their timestamps at `speed`x, yielding batches every POLL_S of wall time."""
    if not rows:
        return
    t0, wall0 = _secs(rows[0]["timestamp_label"]), time.time()
    batch, started = [], None
    for r in rows:
        due = wall0 + (_secs(r["timestamp_label"]) - t0) / speed
        while time.time() < due:
            if batch and time.time() - started >= POLL_S:
                yield batch
                batch = []
            time.sleep(min(0.2, max(0.0, due - time.time())))
        if not batch:
            started = time.time()
        batch.append(r)
    if batch:
        yield batch


def parse_items(text):
    """The JSON object of a board reply, or None for plain text ("...", the wrap-up, a reply that ignored the format)."""
    t = re.sub(r"^```[a-z]*\n|\n?```$", "", text.strip()).strip()
    if not t.startswith("{"):
        return None
    try:
        obj = json.loads(t)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


ECHO_S = (90, 30)  # an "Others" line this many seconds before / after a "You" line can be its echo (Muesli stamps Others ~34 s early)


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


class Echo:
    """Without headphones the mic hears the speakers, so about half the "You" lines on 2026-10-16 repeated the room
    (Others [08:31:29] "Yeah, guys, so--", You [08:32:00] the same) and the model took the room's words for the user's.
    keep() drops a You line whose words mostly appear, in order, in the Others lines around it."""

    def __init__(self):
        self.others = []  # (seconds, words) of recent Others lines

    def keep(self, rows):
        for r in rows:  # both copies arrive in the same poll, so the batch's own Others lines count
            if r["speaker"] != "You":
                self.others.append((_secs(r["timestamp_label"]), _words(r["text"])))
        out = []
        for r in rows:
            if r["speaker"] == "You" and self.echo(_secs(r["timestamp_label"]), _words(r["text"])):
                continue
            out.append(r)
        if self.others:  # the window only ever looks back ECHO_S[0] from the newest line
            newest = max(t for t, _ in self.others)
            self.others = [(t, w) for t, w in self.others if newest - t <= sum(ECHO_S)]
        return out

    def echo(self, t, words):
        heard = [w for s, ws in sorted(self.others, key=lambda x: x[0]) if -ECHO_S[1] <= t - s <= ECHO_S[0] for w in ws]
        if not words or not heard:
            return False
        if len(words) < 3:
            return f" {' '.join(words)} " in f" {' '.join(heard)} "
        pairs, room = list(zip(words, words[1:])), set(zip(heard, heard[1:]))
        return sum(p in room for p in pairs) >= 0.6 * len(pairs)


ONE_TRACK_S = 180  # "You" has talked this long with no "Others" line: the other participants' audio is not reaching Muesli


class Tracks:
    """Watches for the Others track dying (2026-10-16, 09:06: the presenter and the room then arrived only through the
    mic, as "You", for 46 minutes). Runs on the transcript's clock, so a replay sees what the live run saw.
    check() returns "lost" or "back" on a change, else None."""

    def __init__(self):
        self.last_others, self.first, self.lost = None, None, False

    def check(self, rows):
        change = None
        for r in rows:
            t = _secs(r["timestamp_label"])
            self.first = t if self.first is None else self.first
            if r["speaker"] != "You":
                if len(_words(r["text"])) < 3:  # a lone "Bye." leaked through twice while the track was dead
                    continue
                self.last_others = t
                if self.lost:
                    self.lost, change = False, "back"
            elif not self.lost and t - (self.last_others if self.last_others is not None else self.first) > ONE_TRACK_S:
                self.lost, change = True, "lost"
        return change


def format_batch(rows):
    text = "\n".join(f"[{r['timestamp_label']}] {r['speaker']}: {r['text']}" for r in rows)
    return ("[BACKLOG]\n" + text) if rows[0].get("backlog") else text


# ---------------------------------------------------------------- the board page
BOARD_HTML = Path(__file__).resolve().parent / "board" / "index.html"


class BoardHandler(http.server.BaseHTTPRequestHandler):
    """GET / serves the page; GET /events streams the board (a snapshot of open items, then every new event);
    POST /ask sends a question from the page to the session, POST /mark the user's done or wrong on an item, POST /stop ends the run."""
    session = None
    token = secrets.token_hex(16)  # per run, written into the page: another site can POST here but cannot read the page

    def log_message(self, *a):  # the console belongs to the meeting
        pass

    def _local(self):
        """Host is 127.0.0.1 or localhost: a site rebinding its own name to 127.0.0.1 could otherwise read the page."""
        return self.headers.get("Host", "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

    def do_POST(self):
        """/ask {token, q, id, text}: a question typed on the board, or about item id; answered as a say item.
        /mark {token, id, verdict, note}: done retires item id as the model would, wrong takes it off and tells the model why.
        /stop {token}: the board's End button. /record {token}: the banner's Start recording button.
        /send {token, lane, items}: ticked action items to that lane's session. /close {token}: the board closed after the call.
        /wrapup {token}: the wrap-up and packets so far, while the call goes on."""
        try:
            d = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 8000)))
        except ValueError:
            d = None
        get = lambda k: str(d.get(k) or "")
        act = {"/ask": lambda: self.session.ask(get("q"), get("id"), get("text")),
               "/mark": lambda: self.session.mark(get("id"), get("verdict"), get("note")),
               "/stop": lambda: self.session.stop(),
               "/send": lambda: self.session.send_items(get("lane"), get("items")),
               "/wrapup": lambda: self.session.checkpoint(),
               "/close": lambda: self.session.closed.set() or True,
               "/record": lambda: self.session.rec["can_start"] and start_recording(self.session.title)}.get(self.path)
        ok = (act and self._local() and isinstance(d, dict)
              and secrets.compare_digest(get("token").encode(), self.token.encode()) and act())
        self.send_response(204 if ok else 403)
        self.end_headers()

    def do_GET(self):
        if not self._local():
            self.send_error(403)
            return
        if self.path.split("?")[0] != "/events":
            body = BOARD_HTML.read_bytes().replace(b"__TACTICIAN_TOKEN__", self.token.encode()) if BOARD_HTML.exists() else b"board/index.html missing"
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
        s = self.session
        with s.cond:  # snapshot and cursor together: the stream continues where the snapshot stands, no history replay
            snap, seq = list(s.items.values()), len(s.events)
        try:
            for i in snap:  # snapshot: what is open now
                self._emit({"seq": 0, "ts": now(), "kind": {"say": "add"}.get(i["pane"], i["pane"]), **i})
            self._emit({"seq": 0, "ts": now(), "kind": "rec", **s.rec})
            while True:
                with s.cond:
                    s.cond.wait_for(lambda: len(s.events) > seq, timeout=15)
                    new, seq = s.events[seq:], len(s.events)
                for e in new:
                    self._emit(e)
                if not new:  # a named event, so the page knows the link is up through a quiet stretch of the call
                    self.wfile.write(b"event: ping\ndata: {}\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _emit(self, e):
        self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
        self.wfile.flush()


def serve_board(session, port=8770):
    """Serve the board on 127.0.0.1 at the first free port of ten; returns the URL or ''."""
    BoardHandler.session = session
    for p in range(port, port + 10):
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", p), BoardHandler)
        except OSError:
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{p}/"
    return ""


# ---------------------------------------------------------------- Claude session
class Session:
    collecting = False  # a Wrap Up during the call: plain-text replies are its wrap-up and packets
    def __init__(self, project, model, effort, name, log, show_transcript, before=None, pinned=None):
        if not CLAUDE_BIN:
            sys.exit("claude binary not found")
        context, add_dirs, brief = load_context(project)
        if not any(str(project) == d or str(project).startswith(d + os.sep) for d in add_dirs):
            add_dirs.append(str(project))  # a project outside the map's folders gets its own CLAUDE.md loaded
        wrapup = last_wrapup(project, before)
        if wrapup:
            brief = "--- last meeting wrap-up ---\n" + wrapup + ("\n\n" + brief if brief else "")
        self.dirs = [str(project), *add_dirs]  # whose agent sessions count as news
        activity = "" if before else agent_activity(self.dirs, time.time() - 12 * 3600)  # a replay's call is long over
        if activity:
            brief += "\n\n--- recent agent sessions (newest last; newer than the digest) ---\n" + activity
        digest = claude_md(add_dirs)
        if pinned:  # a replay of a run that saved its context: the digest and the brief as they stood at that call
            digest, brief = pinned["digest"], pinned["brief"]
        (LOG_DIR / (name + ".context.json")).write_text(json.dumps({"digest": digest, "brief": brief}) + "\n")
        self.brief_files = [] if before else brief_files(project)  # re-read during the call; a replay keeps its snapshot
        self.brief_seen = {}
        brief_news(self.brief_seen, self.brief_files)
        allow, lookup = (None, "") if before else lookup_prompt()  # a replay cannot ask today's truth about a past call
        prompt = PROMPT.replace("{root}", str(project)).replace("{user}", USER) + ("\n\n" + context if context else "") + (
            "\n\n" + lookup if lookup else "") + (
            "\n\n[PROJECT DIGEST] The CLAUDE.md of the folders you can read, as of this call.\n" + digest if digest else "") + (
            "\n\n[PRE-MEETING BRIEF] Gathered before the call: the last call's wrap-up and recent work. Use it when it answers something; never restate it.\n" + brief if brief else "")
        cmd = [CLAUDE_BIN, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--model", model, "--effort", effort, "--permission-mode", "dontAsk",
               "--setting-sources", "project", "--strict-mcp-config", "--max-turns", "12",
               "--settings", json.dumps(DENY_SETTINGS), "--name", name,
               "--append-system-prompt", prompt]
        cmd += ["--tools", *TOOLS, "--allowedTools", *ALLOWED_TOOLS, *([allow] if allow else []), "--disallowedTools", "Edit", "Write", "--add-dir", *add_dirs]
        # Lean context: the session runs from an empty directory under /private/tmp, so the project's own CLAUDE.md, rules,
        # hooks and skills are not auto-discovered (37k tokens per turn in one large project, used by no note in a measured call); the
        # digest reaches it through the prompt above, not through add-dir loading. A cwd under ~ would load ~/AGENTS.md.
        self.cwd = Path("/private/tmp/tactician") / name
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.show_transcript = show_transcript
        self.archive, self.header, self.ended, self.wrapup = None, "", False, ""  # wrap-up archive: main() sets it once a live call records
        self.stop_asked = False  # End on the board
        self.closed = threading.Event()  # the board closed after the call (or Stop in the menu): the run may exit
        self.screen, self.look = None, False  # (title, jpeg) for the next slice; the model asked for a fresh look
        self.rec = {"state": "waiting", "since": now(), "can_start": False}  # Muesli: waiting, recording, stalled or ended
        self.post = []  # plain-text replies after [MEETING ENDED]: the wrap-up, then the packets
        self.session_id = None
        self.cost = 0.0
        self.turns = 0
        self.rate_status = "allowed"
        self.stderr = open(LOG_DIR / (name + ".stderr.log"), "w")
        self.items, self.n_say, self.n_agenda, self.n_commit, self.events = {}, 0, 0, 0, []  # the board: say, agenda, commit items by id
        self.pending = []  # the user's [WRONG] marks, sent with the next slice
        self.items_log = open(LOG_DIR / (name + ".items.jsonl"), "w")  # every board event, for the page and the evals
        self.cond = threading.Condition()  # wakes the page's event stream; its (reentrant) lock guards self.items across threads
        self.p = subprocess.Popen(cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.stderr, text=True, bufsize=1)
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    # --- output
    def out(self, line, log_only=False):
        if not log_only:
            print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()

    def _reader(self):
        for raw in self.p.stdout:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            t, st = d.get("type"), d.get("subtype")
            if t == "assistant" and d.get("parent_tool_use_id") is None:
                for b in d["message"].get("content", []):
                    if b.get("type") == "text":
                        self._note(b["text"])
                    elif b.get("type") == "tool_use":
                        inp = b.get("input", {})
                        hint = inp.get("command") or inp.get("pattern") or inp.get("query") or inp.get("file_path") or inp.get("url") or ""
                        self.out(c("2", f"{now()}   ⋯ {b['name']} {str(hint)[:90]}"))
            elif t == "result":
                self.turns += 1
                self.cost = d.get("total_cost_usd", self.cost)
                if d.get("permission_denials"):
                    names = ", ".join(x.get("tool_name", "?") for x in d["permission_denials"])
                    self.out(c("2", f"{now()}   denied: {names}"))
                if d.get("is_error"):
                    self.out(c("31", f"{now()} turn error: {d.get('subtype')} {str(d.get('result') or d.get('errors'))[:200]}"))
            elif t == "system" and st == "init" and self.session_id is None:  # re-emitted per idle turn
                self.session_id = d.get("session_id")
                self.out(c("2", f"{now()} session {self.session_id} model={d.get('model')} mode={d.get('permissionMode')}"))
            elif t == "rate_limit_event":
                info = d.get("rate_limit_info", {})
                status = info.get("status", "allowed")
                if status != self.rate_status:
                    self.rate_status = status
                    reset = datetime.fromtimestamp(info.get("resetsAt", 0)).strftime("%a %H:%M") if info.get("resetsAt") else "?"
                    msg = f"{now()} rate limit {status}: {info.get('rateLimitType')} at {int(100 * info.get('utilization', 0))}%, resets {reset}"
                    self.out(c("31" if status == "rejected" else "33", msg))

    def _note(self, text):
        text = text.strip()
        if not text or text == SILENCE:
            return
        obj = parse_items(text)
        if obj is None:  # plain text: the wrap-up and the packets after the end marker, or a reply that ignored the format
            label = "ANSWER" if text.startswith("ANSWER") else "NOTE" if text.startswith("NOTE") else "SAYS"
            self.out("")
            self.out(c("1;32" if label == "ANSWER" else "1;36", f"{now()} {label}") + "\n" + text)
            self.out("")
            if self.ended or self.collecting:
                with self.cond:
                    self.post.append(text)
                    self.cond.notify_all()
            return
        with self.cond:
            self._apply(obj)

    def _apply(self, obj):
        says = [it for it in (obj.get("say") or []) if isinstance(it, dict)]
        # three a reply, loudest first: on 2026-10-16 two answers posted 5 and 6 items, too many to read, all gone together
        says = sorted(says, key=lambda it: -max(1, min(3, int(it.get("importance") or 1))))[:SAY_MAX]
        answer, self.answering = getattr(self, "answering", False) and bool(says), False
        for it in says:
            lines = [str(l).strip() for l in (it.get("lines") or []) if str(l).strip()][:3]
            if not lines:
                continue
            self.n_say += 1
            item = {"id": f"s{self.n_say}", "pane": "say", "lines": lines, "source": str(it.get("source") or ""),
                    "importance": max(1, min(3, int(it.get("importance") or 1))), "born": time.time(), **({"answer": True} if answer else {})}
            self.items[item["id"]] = item
            self.event("add", item)
            self.out("")  # logged as a NOTE block so the scorer and the judge read it like any other run
            self.out(c({3: "1;31", 2: "1;33", 1: "1;36"}[item["importance"]], f"{now()} NOTE") + f"\nNOTE: [{item['importance']}] {item['id']} "
                     + " | ".join(lines) + (f" ({item['source']})" if item["source"] else ""))
            self.out("")
        moved = []
        for it in obj.get("agenda") or []:
            aid = str(it.get("id") or "").strip()
            while not aid:  # a new item without an id gets the next free one
                self.n_agenda += 1
                aid = f"a{self.n_agenda}" if f"a{self.n_agenda}" not in self.items else ""
            old = self.items.get(aid, {})
            item = {"id": aid, "pane": "agenda", "text": str(it.get("text") or old.get("text") or "").strip(),
                    "rank": int(it.get("rank") or old.get("rank") or 99), "born": old.get("born", time.time())}
            if item["text"]:
                self.items[aid] = item
                moved.append(item)
        if moved:  # ranks are positions: a moved item takes its place and the others shift down
            cur = sorted((i for i in self.items.values() if i["pane"] == "agenda" and "done" not in i), key=lambda i: i["rank"])
            for m in moved:
                cur.remove(m)
                cur.insert(max(0, m["rank"] - 1), m)
            for n, i in enumerate(cur, 1):
                i["rank"] = n
                self.event("agenda", i)
            self.out(c("2", f"{now()}   agenda: " + ", ".join(f"{i['id']} {i['text']}" for i in cur)))
        for it in obj.get("commit") or []:
            cid = str(it.get("id") or "").strip()
            if not cid.startswith("c") or self.items.get(cid, {}).get("pane", "commit") != "commit":
                cid = ""
            while not cid:  # a new commitment without a usable id gets the next free one
                self.n_commit += 1
                cid = f"c{self.n_commit}" if f"c{self.n_commit}" not in self.items else ""
            text, old = str(it.get("text") or "").strip(), self.items.get(cid, {})
            if text and text != old.get("text"):
                self.items[cid] = {"id": cid, "pane": "commit", "text": text, "born": old.get("born", time.time())}
                self.event("commit", self.items[cid])
                self.out(c("2", f"{now()}   commit: {cid} {text}"))
        if obj.get("look"):  # the model wants to see what the room is pointing at: the next slice brings a fresh image
            self.look = True
        done = [i for i in (obj.get("done") or []) if self.retire(str(i), "done")]
        if done:
            self.out(c("2", f"{now()}   done: " + ", ".join(done)))

    # --- the board
    def event(self, kind, data, log=True):
        e = {"seq": len(self.events) + 1, "ts": now(), "kind": kind, **data}
        with self.cond:
            self.events.append(e)
            self.cond.notify_all()
        if log:
            self.items_log.write(json.dumps(e) + "\n")
            self.items_log.flush()

    def retire(self, iid, why, by=None):
        with self.cond:
            item = self.items.get(iid)
            if not item or "done" in item:
                return False
            if item["pane"] == "agenda":  # raised: it stays, struck through under the open items, until re-ranked
                item["done"] = time.time()
            else:
                del self.items[iid]
            self.event(why, {k: item[k] for k in ("id", "pane", "done") if k in item} | ({"by": by} if by else {}))
            return True

    def mark(self, iid, verdict, note=""):
        """The user's verdict from the board: done retires the item as the model would; wrong takes it off the board and
        tells the model with the next slice, with their reason when they typed one (without it the model cannot tell a wrong fact
        from a useless one). Both are logged, so the review starts from what they decided in the call."""
        with self.cond:
            item = self.items.get(iid)
            if not item or verdict not in ("done", "wrong"):
                return False
            if verdict == "done":
                self.retire(iid, "done", by="user")
            else:
                del self.items[iid]
                self.event("wrong", {"id": iid, "pane": item["pane"], "by": "user"})
                note = " ".join(note.split())[:300]
                self.pending.append(f'[WRONG] {USER} marked {iid} wrong: "{" ".join(item.get("lines") or [item.get("text", "")])}"'
                                    + (f" His reason: {note}" if note else ""))
        self.out(c("2", f"{now()}   atta marked {iid} {verdict}" + (f": {note}" if verdict == "wrong" and note else "")))
        return True

    def stop(self):
        """End on the board: interrupt main() as Ctrl-C does, a moment later so the page gets its answer first; main() then
        wraps up if a live call was recording. A wrap-up already under way (the call ended in Muesli) is left to finish."""
        self.closed.set()
        if not (self.stop_asked or self.ended):
            self.stop_asked = True
            threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT)).start()
        return True

    def send_items(self, lane, items):
        """Items the user ticked on the end screen go to that lane's project session: the cmux workspace whose folder is
        the lane's (general: the project root), or the workspace title config [sessions] names when several share it.
        One line, so the session's prompt takes it as one message; the packet holds the context. Each send is recorded
        next to the packet. True when it was typed into the session."""
        items = " | ".join(" ".join(i.split()) for i in items.splitlines() if i.strip())
        if not (items and self.archive):
            return False
        want, env = CONFIG.get("sessions", {}).get(lane), {**os.environ, "CMUX_QUIET": "1"}
        here = lambda d: os.path.realpath(os.path.expanduser(d)).lower()  # case-insensitive disk, /var is /private/var
        folders = {here(r if lane == "general" else r / lane) for r in ROOTS}
        try:
            ws = json.loads(subprocess.run(["cmux", "workspace", "list", "--json"], capture_output=True, text=True, timeout=10, env=env).stdout)
            ws = ws if isinstance(ws, list) else ws.get("workspaces", [])
            hits = [w for w in ws if (want in str(w.get("title")) if want else here(w.get("current_directory") or "/-") in folders)]
            if len(hits) != 1:  # none open, or several in one folder and no [sessions] entry to choose
                return False
            ref, name = hits[0]["ref"], hits[0].get("title")
            packet = self.archive.with_name(f"{self.archive.stem}-packet-{lane}.md")
            msg = (f"Action items from the {self.archive.stem} call, approved by {USER}: {items} -- Context: {packet}. "
                   "Triage, gather what you need and propose; stop before anything client-facing or any commitment.")
            ok = all(subprocess.run(["cmux", *cmd], capture_output=True, timeout=10, env=env).returncode == 0
                     for cmd in (["send", "--workspace", ref, msg], ["send-key", "--workspace", ref, "enter"]))
        except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
            return False
        if ok:
            with self.archive.with_name(f"{self.archive.stem}-sent-{lane}.md").open("a") as f:
                f.write(f"{now()} sent to {name} ({ref}):\n{msg}\n\n")
            self.out(c("2", f"{now()} sent {lane} items to {name}"))
        return ok

    def linger(self, minutes=20):
        """After a saved wrap-up the board stays served, so its Send buttons work, until Close window, Stop in the menu
        or twenty minutes."""
        try:
            self.closed.wait(minutes * 60)
        except KeyboardInterrupt:
            pass

    def set_rec(self, state, **extra):
        """Muesli's recording state, for the board's banner; an event only on a change."""
        if state != self.rec["state"] or extra:
            self.rec = {**self.rec, **extra, "state": state, "since": now()}
            self.event("rec", self.rec)

    def expire(self, max_age=240, answer_age=900):
        # ponytail: a say item the model never retired leaves after four minutes, an answer to the user's question after
        # fifteen (they asked for it, and may read it later); the model or the user retires either sooner
        with self.cond:
            for i in [i for i in self.items.values() if i["pane"] == "say"
                      and time.time() - i["born"] > (answer_age if i.get("answer") else max_age)]:
                self.retire(i["id"], "expire")

    def board_line(self):
        """What the model is told is on the board, with ids, so it retires instead of re-posting."""
        with self.cond:
            items = list(self.items.values())
        say = [f'{i["id"]} "{" ".join(" ".join(i["lines"]).split()[:6])}"' for i in items if i["pane"] == "say"]
        ag = sorted((i for i in items if i["pane"] == "agenda"), key=lambda i: i["rank"])
        agenda = [f'{i["id"]} "{i["text"]}"' for i in ag if "done" not in i]
        raised = [f'{i["id"]} "{" ".join(i["text"].split()[:6])}"' for i in ag if "done" in i]
        cm = [f'{i["id"]} "{i["text"]}"' for i in items if i["pane"] == "commit"]
        return (f"[BOARD] say: {', '.join(say) or 'none'}; agenda: {', '.join(agenda) or 'none'}"
                + (f"; raised: {', '.join(raised)}" if raised else "")
                + (f"; commitments: {', '.join(cm)}" if cm else "")) if say or ag or cm else ""

    # --- input
    def send(self, text, label=None, priority=None, image=None):
        content = text if not image else [{"type": "text", "text": text}, {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": __import__("base64").b64encode(image).decode()}}]
        msg = {"type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": content}}
        if priority:  # "later" = hold until the current turn ends; default injects at the next tool boundary
            msg["priority"] = priority
        with self.lock:
            if self.p.poll() is not None:
                return False
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
        if label:
            self.out(c("2", f"{now()} {label}"))
        return True

    def ask(self, q, iid="", text=""):
        """The user's direct question: typed in the console, or typed or clicked on the board (about item iid)."""
        q, text = " ".join(q.split())[:500], " ".join(text.split())[:300]
        if not (q or iid):
            return False
        line = (f'About {iid} "{text}": ' if iid else "") + (q or "more detail, the facts behind it")
        if self.rec["state"] != "recording":  # the board shows it too; the answer must not pretend to have heard the call
            line += " (Muesli is not transcribing right now: nothing new from the call has reached you; say so if they seem to expect it.)"
        self.answering = True  # the next say items answer it: they stay up longer
        return self.send(f"[USER] {line}", label=f"you asked: {line}")

    def send_transcript(self, rows):
        text = format_batch(rows)
        for r in rows:
            self.out(c("2", f"  [{r['timestamp_label']}] {r['speaker']}: {r['text']}"), log_only=not self.show_transcript)
            self.event("transcript", {"t": r["timestamp_label"], "who": r["speaker"], "text": r["text"]}, log=False)
        self.expire()
        with self.cond:
            tail, self.pending = self.pending + [self.board_line()], []
            screen, self.screen = self.screen, None
        if screen:
            tail.append(f"[SCREEN] The meeting window ({screen[0]}) at {now()}, attached.")
        return self.send(text + "".join("\n" + t for t in tail if t), image=screen and screen[1])

    def _ask_after(self, msg, label, n, timeout):
        """Send a post-meeting request (held until the current turn ends) and wait for its plain-text reply, the n-th."""
        self.send(msg, label=label, priority="later")
        with self.cond:
            self.cond.wait_for(lambda: len(self.post) >= n or self.p.poll() is not None, timeout)
        return self.post[n - 1] if len(self.post) >= n else ""

    def _wrap(self, ask, label, packets_msg, timeout):
        """The wrap-up and, on a live call, one packet file per lane next to it: (wrapup, {lane: body})."""
        n, packets = len(self.post), {}
        wrapup = self._ask_after(ask, label, n + 1, timeout)
        if wrapup and self.archive:
            packets = split_packets(self._ask_after(packets_msg.replace("{user}", USER), "asking for the action packets", n + 2, timeout))
            self.archive.parent.mkdir(parents=True, exist_ok=True)
            for lane, body in packets.items():
                path = self.archive.with_name(f"{self.archive.stem}-packet-{lane}.md")
                path.write_text(f"{self.header}\nAction packet for {lane}; the wrap-up is in {self.archive.name}.\n\n{body}\n")
                self.out(c("2", f"{now()} packet archived: {path}"))
        return wrapup, packets

    def checkpoint(self):
        """Wrap Up on the board: the wrap-up and the packets so far, shown for approval while the call goes on (the
        live notes pause while the model writes them). The end of the call writes the final ones."""
        if self.ended or self.collecting or not self.archive:
            return False

        def run():
            self.collecting = True
            try:
                w, packets = self._wrap(f"[WRAP-UP SO FAR] {USER} asked for the wrap-up of the call so far; the call is still going. "
                                        "Reply in plain text exactly as for [MEETING ENDED], then go back to following the call.",
                                        "asked for the wrap-up so far", PACKETS_MSG.replace("The call is over.", "The call is still going: cover it so far."), 180)
            finally:
                self.collecting = False
            self.event("summary", {"wrapup": w, "packets": packets})
        threading.Thread(target=run, daemon=True).start()
        return True

    def finish(self, wrapup=True, timeout=180):
        packets = {}
        if self.p.poll() is None and wrapup:
            self.ended = True
            self.wrapup, packets = self._wrap("[MEETING ENDED] Give the wrap-up now.", "meeting ended, asking for the wrap-up", PACKETS_MSG, timeout)
        try:
            with self.lock:
                self.p.stdin.close()
            self.p.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            self.p.terminate()
        self.stderr.close()
        if self.wrapup and self.archive:
            self.archive.parent.mkdir(parents=True, exist_ok=True)
            self.archive.write_text(f"{self.header}\n\n{self.wrapup}\n")
            self.out(c("2", f"{now()} wrap-up archived: {self.archive}"))
        self.out(c("2", f"{now()} done: {self.turns} turns, est. ${self.cost:.3f}; resume with: cd {self.cwd} && claude --resume {self.session_id}"))
        saved = bool(self.wrapup and self.archive)  # the board's last word: it shows the wrap-up and packets, End just closes it
        self.set_rec("done", saved=saved, **({"wrapup": self.wrapup, "packets": packets} if saved else {}))
        self.items_log.close()
        time.sleep(1.5)  # ponytail: time for the event stream to deliver it before the process (and its server) exits


def typed_input(session):
    """Lines typed in the console become direct questions."""
    for line in sys.stdin:
        line = line.strip()
        if line:
            session.ask(line)


# ---------------------------------------------------------------- main
def resolve_project(arg):
    for cand in (Path(arg).expanduser(), *(r / arg for r in ROOTS)):
        if cand.is_dir():
            return cand.resolve()
    sys.exit(f"project folder not found: {arg}")


def probe():
    m = wait_for_recording(print)
    print(f"{now()} recording: meeting {m['id']} started {m['start_time']}")
    for rows in tail_live(m["id"], print):
        for r in rows:
            lag = (datetime.now() - datetime.now().replace(
                hour=int(r["timestamp_label"][:2]), minute=int(r["timestamp_label"][3:5]),
                second=int(r["timestamp_label"][6:8]))).total_seconds()
            print(f"{now()} +{lag:5.1f}s  [{r['timestamp_label']}] {r['speaker']}: {r['text'][:100]}")


def main():
    global POLL_S
    signal.signal(signal.SIGINT, signal.default_int_handler)  # a launcher's background job starts with SIGINT ignored
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", default=CONFIG.get("default_project"), help="project folder (default: config default_project)")
    ap.add_argument("--replay", metavar="MEETING_ID|LOG", help="replay a completed Muesli meeting, or a past run's log file")
    ap.add_argument("--speed", type=float, default=20.0, help="replay speed multiplier (default 20)")
    ap.add_argument("--from", dest="t_from", metavar="HH:MM:SS", help="replay: first timestamp")
    ap.add_argument("--to", dest="t_to", metavar="HH:MM:SS", help="replay: last timestamp")
    ap.add_argument("--probe", action="store_true", help="tail Muesli only, print lag, no Claude")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--poll", type=float, default=POLL_S, help="seconds between transcript batches")
    ap.add_argument("--show-transcript", action="store_true", help="echo transcript lines in the console")
    ap.add_argument("--with", dest="participants", metavar="NAMES", help='who is on the call, e.g. "Sam (manager, not technical), Pat (tech lead)"')
    ap.add_argument("--port", type=int, default=8770, help="board page port (the first free of ten from here)")
    ap.add_argument("--shell-config", action="store_true", help="print the tenant settings as shell variables (the menu bar)")
    ap.add_argument("--make-shortcut", action="store_true", help="create the shortcut Tactician uses to start Muesli recordings")
    a = ap.parse_args()
    if a.make_shortcut:
        return make_shortcut()
    if a.shell_config:
        q = shlex.quote
        print(f"TACTICIAN_HOME={q(str(HOME))}; DEFAULT_PROJECT={q(CONFIG.get('default_project', ''))}; "
              f"PARTICIPANTS={q(CONFIG.get('participants', ''))}; PROFILE={q(CONFIG.get('browser_profile', 'Default'))}; "
              f"ROOTS=({' '.join(q(str(r)) for r in ROOTS)})")
        return 0
    POLL_S = a.poll

    if a.probe:
        return probe()
    if not a.project:
        sys.exit(f"name a project, or set default_project in {HOME / 'config.toml'}")
    project = resolve_project(a.project)
    LOG_DIR.mkdir(exist_ok=True)
    pinned = None
    if a.replay:
        m = load_transcript(int(a.replay)) if a.replay.isdigit() else load_log(a.replay)
        rows = parse_transcript(m["raw_transcript"], a.t_from, a.t_to)
        snap = Path(a.replay).with_suffix(".context.json")
        pinned = json.loads(snap.read_text()) if not a.replay.isdigit() and snap.exists() else None
        if not pinned:
            print(c("33", f"{now()} no context snapshot for {a.replay}: this replay reads today's digest and brief, which may know the outcome"))
        start = local_time(m["start_time"])
        a.participants = a.participants or m.get("participants")  # a log replay keeps its run's participants unless --with
    else:  # ponytail: no calendar signal to pick the project, so say it loudly; Ctrl-C before the end archives nothing
        print(c("1", f"{now()} Tactician for {project.name}. Not this meeting's project? Ctrl-C and run: tactician <project>"))
        m, start = None, datetime.now().strftime("%Y-%m-%d %H:%M")  # warm-up: the board comes up now, Muesli's start replaces this
    base = f"tactician-{project.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}" + (f"-replay{m['id']}" if a.replay else "")
    name, n = base, 1
    while True:  # exclusive create: two runs started in the same second must not share a log
        try:
            log = open(LOG_DIR / (name + ".md"), "x"); break
        except FileExistsError:
            n += 1; name = f"{base}-{n}"
    opts = {k: v for k, v in vars(a).items() if k != "project" and v not in (None, False)}
    log.write(f"# {name}\nproject: {project}\nstarted: {datetime.now().isoformat(timespec='seconds')}\n"
              + (f"meeting: {m['id']} at {start}\n" if m else "") + f"args: {json.dumps(opts)}\n\n")
    session = Session(project, a.model, a.effort, name, log, a.show_transcript,
                      before=start[:10] if a.replay else None, pinned=pinned)  # a replay's brief stops before the call it replays
    session.title = f"{project.name} call"  # the Muesli recording's title when Tactician starts it
    signal.signal(signal.SIGTERM, lambda *_: session.stop())  # Stop in the menu: as End on the board, with the wrap-up
    url = serve_board(session, a.port)
    session.out(c("2", f"{now()} board: {url or 'no free port'}"))
    if url and sys.stdin.isatty():  # the configured Chrome profile, as an app window on the side monitor
        subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--profile-directory={CONFIG.get('browser_profile', 'Default')}", f"--app={url}"])
    day0 = datetime.strptime(start[:10], "%Y-%m-%d")  # the model gets weekdays from this, never by computing them (it got 10-13 wrong)
    cal = ", ".join(f"{day0 + timedelta(d):%a %m-%d}" for d in range(-7, 15))
    ctx = f"[CONTEXT] Call date {start[:10]}." + (f" Participants: {a.participants}. Use their names instead of speaker labels." if a.participants else "") + (
        "" if a.replay else f" The call has not started yet; until its transcript arrives, {USER} may ask questions to prepare.")
    session.send(f"{ctx} Calendar: {cal}. Reply with the initial agenda.", label=ctx[10:])  # the console skips the calendar
    if sys.stdin.isatty():
        threading.Thread(target=typed_input, args=(session,), daemon=True).start()
    try:
        if a.replay:
            session.out(c("2", f"{now()} replaying {a.replay}: {len(rows)} lines at {a.speed:g}x into {project}"))
            feed = tail_replay(rows, a.speed)
            session.set_rec("recording")
        else:
            joined = active_meeting()  # a recording already running (an earlier call that never stopped it) is joined, not replayed
            owns = False  # Tactician started this recording, so ending the run ends it too
            if not joined:  # starting Tactician starts the recording, through Muesli's own Shortcuts action
                can = has_shortcut()
                session.set_rec("waiting", can_start=can)
                if not can:
                    session.out(c("33", f"{now()} Muesli is not recording and there is no '{SHORTCUT}' shortcut (make it once: tactician --make-shortcut): start the recording in Muesli"))
                elif start_recording(session.title):
                    owns = True
                    session.out(c("2", f"{now()} asked Muesli to start recording '{session.title}'"))
                else:
                    session.out(c("33", f"{now()} the '{SHORTCUT}' shortcut failed: start the recording in Muesli"))
            m = wait_for_recording(print)
            session.set_rec("recording")
            start = local_time(m["start_time"])
            session.out(c("2", f"{now()} recording detected: meeting {m['id']} (started {start})"))
            session.out(f"meeting: {m['id']} at {start}", log_only=True)  # after a status line, so no parser reads it as part of a note
            # replays never overwrite the record of a real meeting, so only a live run archives
            session.archive = MEETINGS_DIR / project.name / f"{datetime.now():%Y-%m-%d-%H%M}.md"
            session.header = f"# {project.name} meeting {start} (Muesli {m['id']})" + (f", with {a.participants}" if a.participants else "")
            # a run joining a recording in progress starts two minutes back (Muesli stamps the others' track ~34 s early):
            # on 2026-10-16 two restarts re-read everything since 08:30 and carried call 1's commitments into call 3
            skip = (datetime.now() - timedelta(seconds=120)).strftime("%H:%M:%S") if joined else None
            if skip:
                session.out(c("2", f"{now()} joined a recording running since {start}: transcript before {skip} is an earlier call, skipped"))
                with session.cond:
                    session.pending.append(f"[CONTEXT] This run joined a recording that has been running since {start[11:]}. "
                                           f"Transcript before {skip} belongs to an earlier call and is not shown; this call starts here.")
            session.owns_recording = owns or "tactician" in (m["title"] or "").lower() or m["title"] == session.title
            feed = tail_live(m["id"], print, skip_before=skip)
        last_row = time.time()
        # News from the agent sessions rides with the next slice. A replay reads them as of its own transcript clock: the
        # session files only grow, so it sees what the live run would have seen.
        day = start[:10]
        clock = (lambda rows: time.time()) if not a.replay else (lambda rows: datetime.strptime(
            f"{day} {rows[-1]['timestamp_label']}", "%Y-%m-%d %H:%M:%S").timestamp() if rows else last_upd)
        last_upd = time.time() if not a.replay else datetime.strptime(start, "%Y-%m-%d %H:%M").timestamp()
        echo, tracks = Echo(), Tracks()
        call_at = None  # live runs: the last time a call app (not Muesli) had the microphone
        looked, seen, why_not = 0, None, ""  # the meeting window: last look, fingerprint of the last image sent, why none
        shots = LOG_DIR / f"{name}.screens"  # every image the session saw, kept with the run's log
        for rows in feed:
            if not a.replay and (session.look or time.time() - looked > SCREEN_S):
                looked, session.look = time.time(), False
                wid, title = call_window()
                if wid:
                    shots.mkdir(exist_ok=True)
                    f = shots / f"{datetime.now():%H%M%S}.jpg"
                    fp = capture_window(wid, f)
                    if fp and changed(fp, seen):
                        seen = fp
                        with session.cond:
                            session.screen = (title, f.read_bytes())
                        session.out(c("2", f"{now()} screen: {title} ({f.name})"))
                    else:
                        f.unlink(missing_ok=True)
                elif title != why_not and title != "no meeting window on screen":
                    session.out(c("33", f"{now()} screen: {title} (System Settings, Privacy, Screen Recording: allow the app that starts Tactician)"))
                why_not = title if not wid else ""
            if not a.replay and call_at is not False:
                if [u for u in mic_users() if not u.startswith("com.muesli")]:
                    call_at = time.time()
                elif call_at and time.time() - call_at > CALL_GONE_S:
                    call_at = False  # once: the recording ends, the feed with it, and the wrap-up follows
                    ok = stop_recording()
                    session.out(c("2" if ok else "33", f"{now()} the call app let go of the microphone: " + (
                        "asked Muesli to stop recording" if ok else f"stop the recording in Muesli (no '{STOP_SHORTCUT}' shortcut: tactician --make-shortcut)")))
            t = clock(rows)
            if t - last_upd > UPDATE_S:
                upd, last_upd = agent_activity(session.dirs, last_upd, until=t), t
                news = brief_news(session.brief_seen, session.brief_files)
                if news:
                    with session.cond:
                        session.pending.append("[UPDATE] New in the brief's files since your last look (the source of truth moved on):\n" + news)
                    session.out(c("2", f"{now()} update from the brief files: {news.count(chr(10)) + 1} lines"))
                if upd:
                    with session.cond:
                        session.pending.append(f"[UPDATE] What {USER} and their agent sessions said since your last look, newest last:\n" + upd)
                    session.out(c("2", f"{now()} update from the agent sessions: {upd.count(chr(10)) + 1} lines"))
            if not rows:  # a quiet poll: after STALL_S the board says the transcript has stopped
                if time.time() - last_row > STALL_S:
                    session.set_rec("stalled")
                continue
            last_row = time.time()
            session.set_rec("recording")
            track = tracks.check(rows)
            if track:
                at = rows[-1]["timestamp_label"]
                with session.cond:
                    session.pending.append(
                        f"[AUDIO] Since about {at} only the \"You\" track arrives: the other participants' audio is not being "
                        f"transcribed, and the microphone hears them through the speakers. Until a line from \"Others\" returns, "
                        f"a \"You\" line can be anyone: treat it as the room unless the content makes it certain {USER} is speaking."
                        if track == "lost" else f"[AUDIO] The other participants' track is back at {at}: \"You\" is {USER} again.")
                session.set_rec("recording", one_track=track == "lost", track_at=at)
                session.out(c("33", f"{now()} audio: the Others track is " + ("silent, only the mic is heard" if track == "lost" else "back")))
            rows = echo.keep(rows)
            if not rows:  # every line was the mic hearing the speakers
                continue
            if not session.send_transcript(rows):
                print(c("31", f"{now()} claude exited (code {session.p.returncode}); see logs/{name}.stderr.log"))
                return 1
        session.set_rec("ended")
        session.finish()
    except KeyboardInterrupt:  # Ctrl-C: no wrap-up; End on the board or Stop in the menu during a call: wrap up what it heard
        wrap = session.stop_asked and session.archive is not None
        print(c("2", f"\n{now()} " + ("ended, writing the wrap-up" if wrap else "interrupted, closing session")))
        if wrap and getattr(session, "owns_recording", False):  # the next call then gets a recording of its own
            ok = stop_recording()
            session.out(c("2" if ok else "33", f"{now()} " + ("asked Muesli to stop recording" if ok else "stop the recording in Muesli")))
        session.finish(wrapup=wrap, timeout=180 if wrap else 20)
    finally:
        log.close()
    if session.rec.get("saved"):
        session.linger()
    return 0


if __name__ == "__main__":
    sys.exit(main())
