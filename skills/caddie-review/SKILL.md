---
name: caddie-review
description: Post-call review loop for Caddie, the live meeting prompter. Grades the newest live run with the LLM judge, walks the user through the graded board items and the missed moments to collect their own verdicts, writes them into the meeting's eval file, folds repeated failures into Caddie's prompt, proves the change with one judged replay, and runs the tenant's post-call steps (digest, call queue). Use after a Caddie call, or when the user says "review the call", "review caddie", "grade the run", "how did caddie do", or reacts to something Caddie showed them in a meeting.
---

# Caddie review

Goal: the same day as the call, while the user still remembers the room, turn the run into three durable things:
an eval file for this meeting that encodes their judgment, a prompt that has learned from any repeated failure
(proven by a judged replay, not assumed), and engagement state that carries the call's outcome into the next brief.

Code: the caddie checkout this skill is linked from; read its `README.md` first for the commands. Data: the tenant
folder `~/.caddie` (or `$CADDIE_HOME`), and its `NOTES.md` for this company's post-call steps and constraints.
The run is the newest `~/.caddie/logs/caddie-<project>-<stamp>.md` without `-replay` in its name that has a
`meeting: <Muesli id> at <local time>` line (logged when Muesli starts recording; a log without one is a warm-up
stopped before any call, so skip it); board events are in the matching `.items.jsonl`; the wrap-up and the per-lane
packets are in `~/.caddie/meetings/<project>/<date>-<HHMM>*.md`.

## What good looks like

- **Judge first, then the user.** `python3 evals/judge.py ~/.caddie/logs/<run>.md` (background it: a few minutes,
  about $0.25) grades every item and reports missed moments; it uses `~/.caddie/evals/meeting-<id>.toml` when one
  exists, else none. Read each "wrong" reason before trusting it: documents move after a call, and the judge can
  mistake that drift for an error.
- **Start from what the user already decided in the call.** Board events with `"by": "user"` (`"atta"` in older
  logs) in the `.items.jsonl` (also `<name> marked <id> done|wrong` lines in the log) are their verdicts at the
  moment: `wrong` is a confirmed bad item, so record it without asking again and ask only why when the reason is not
  obvious; `done` from them on an item the model left open means the model was slow to retire it. `you asked:` lines
  show where the board was not enough. `commit` events are the commitments Caddie caught; check them against the
  wrap-up and the transcript.
- **The user grades what only they can.** They know what was said in the room, what landed and what distracted them.
  Show the graded table compactly (item, meeting time, importance, lines, judge verdict), then ask through
  AskUserQuestion, up to four questions per call, red items first: was it right, was it useful at that moment,
  was the colour right (red is only for the user being wrong or about to commit against a promise), was the wording
  right for whoever it answered. Offer the judge's missed moments as questions too, and always ask what Caddie should
  have said that neither caught. Stop asking when the answers stop changing anything.
- **The eval file encodes their verdicts.** Create or extend `~/.caddie/evals/meeting-<id>.toml` in the format of the
  existing ones there: an `[[expect]]` for every miss they confirm and for every item they valued (so a regression
  shows), with meeting-time windows, `any` regexes broad enough for paraphrase, and a `source` you opened yourself;
  `[[silence]]` for stretches where a post was noise; `[[stale]]` for superseded figures that appeared. Record their
  per-item grades as `[[atta]]` tables (`item`, `verdict`, `comment`) so the judge can be calibrated against them.
- **The prompt changes only for patterns.** Edit `PROMPT` in `caddie.py` when a failure repeats or a red-level
  one happened once (a wrong correction, a missed date conflict, a repeated caveat). Write the rule as a
  principle; project facts belong in the digest, never in the prompt. Then replay once with the same
  participants (`python3 caddie.py <project> --replay ~/.caddie/logs/<run>.md --speed 3 --poll 5 --with "..."`,
  about 6 minutes and $0.65, background it) and judge the replay with the new eval. Keep the change only if recall
  holds and what they flagged improved; otherwise revert it. Replays read today's documents, so a replay can know
  things the live run could not: compare the verdicts, not only the totals.
- **The call's outcome reaches the next brief.** Run the post-call steps in `~/.caddie/NOTES.md` (for example:
  fold the wrap-up and the packets into the engagement digest's commitments, delete the call-queue lines the
  wrap-up shows as raised).
- **Leave a record.** Append the graded run, their verdicts, what changed and the before/after judge numbers
  to a dated `~/.caddie/docs/live-<date>.md`; commit prompt changes in the caddie repo.

## Constraints

- Read-only on project repos: any repo `NOTES.md` names gets the same `git status --porcelain | wc -l` before and
  after. The judge and replays never write there.
- Nothing client-facing leaves this skill. Packets go to project sessions only when the user hands them over.
- No client content enters the caddie repo, ever: logs, wrap-ups, eval files and call notes live in `~/.caddie`.
  Prompt rules are principles, never client facts. Keep transcript text in eval files to short trigger phrases.
- Check the rate-limit window before replay batches; one replay per change is the norm, two when the judge's
  verdicts disagree with the user's.
