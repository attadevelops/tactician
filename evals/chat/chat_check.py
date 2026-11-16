#!/usr/bin/env python3
"""End-to-end check of the chat rules with the real model: replays seminar.md with seminar.chat (an invented workshop)
and checks the board. Priya's question about invoices after cutoff arrives during bank reconciliations: it must be held
(a quiet chat note), raised as a "Good moment" nudge only once the talk reaches accruals (10:04:30), and followed by a
"Check with Priya" once the presenter answers it (10:05:45). Poll letters, Y and thanks must never become questions.
  python3 evals/chat/chat_check.py [--speed 4]     (about 4 minutes; uses your Claude plan)"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
speed = sys.argv[sys.argv.index("--speed") + 1] if "--speed" in sys.argv else "4"
home = Path(tempfile.mkdtemp())  # hermetic: an empty tenant, never the machine's own
(home / "config.toml").write_text('user = "Alex"\n')
(home / "workshop").mkdir()
r = subprocess.run([sys.executable, str(HERE.parent.parent / "tactician.py"), str(home / "workshop"), "--replay", str(HERE / "seminar.md"),
                    "--chat-replay", str(HERE / "seminar.chat"), "--speed", speed, "--poll", "5", "--port", "8850"],
                   env={**__import__("os").environ, "TACTICIAN_HOME": str(home)}, capture_output=True, text=True, timeout=900)
log = next((home / "logs").glob("tactician-workshop-*.md"))
items = {e["id"]: e for e in map(json.loads, next((home / "logs").glob("*.items.jsonl")).read_text().splitlines()) if e["kind"] == "add"}

heard, posted = "00:00:00", []  # (id, the newest transcript line sent when the item was posted)
for line in re.sub(r"\x1b\[[0-9;]*m", "", log.read_text()).splitlines():
    if m := re.match(r"^  \[(\d\d:\d\d:\d\d)\] ", line):
        heard = m[1]
    elif m := re.match(r"^NOTE: \[\d\] (s\d+) ", line):
        posted.append((m[1], heard))
show = [(heard, items[i].get("note", False), items[i].get("chat", False), " | ".join(items[i]["lines"])) for i, heard in posted]
for row in show:
    print(*row, sep="  ")
text = lambda row: row[3].lower()
held = [r for r in show if r[1] and r[2] and "priya" in text(r)]
nudge = [r for r in show if "good moment" in text(r) and "priya" in text(r)]
check = [r for r in show if "check with priya" in text(r)]
noise = [r for r in show if re.search(r"\b(dana|mo|lee)\b", text(r))]
fails = [msg for ok, msg in (
    (held and held[0][0] < "10:04:30", "Priya's question held as a chat note while the talk was on bank reconciliations"),
    (not [r for r in nudge if r[0] < "10:04:30"], "no nudge before the talk reached accruals"),
    (nudge, "a 'Good moment' nudge for Priya's question once the talk reached accruals"),
    (check and check[0][0] >= "10:05:45", "a 'Check with Priya' after the presenter answered"),
    (not noise, "poll letters, Y and thanks never became items"),
) if not ok]
print(f"\nlog: {log}")
for f in fails:
    print("FAIL:", f)
print("chat check ok" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
