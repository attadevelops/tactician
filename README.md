# <img src="logo.svg" width="36" alt=""> Tactician

A live meeting prompter for macOS. While you are on a call, Tactician follows the transcript and puts what to say on a
board beside you, grounded in your own project folders, your engagement notes and what your coding-agent sessions have
been doing. It warns you before you say something that contradicts the contract or a promise you made, keeps the
agenda, lists what you committed to, and writes a wrap-up and per-project action packets when the call ends.

Muesli (<https://github.com/muesli-hq/muesli>) records and transcribes the call locally. Tactician tails Muesli's live
transcript rows (read-only) into one long-lived headless Claude Code session per call, with read access to the project
you name. Requirements: an Apple Silicon Mac, Muesli, Claude Code signed in with your own subscription, Python 3.11+
(stdlib only), and optionally SwiftBar for the menu bar item.

```
tactician                               # the default project's call: board up at once, starts Muesli, exits when the call ends
tactician acme-web --show-transcript    # also echo the transcript lines
tactician acme-web --with "Sam (PM, not technical), Pat (developer)"   # who is on the call, with roles
tactician acme-web --replay 48 --from 11:50:00 --to 12:05:00 --speed 20   # replay a finished Muesli meeting
tactician acme-web --replay ~/.tactician/logs/<run>.md --speed 3 --poll 5   # replay a past run's own log
tactician --probe                       # measure Muesli's transcript lag, no Claude
python3 test_tactician.py               # self-check, no meeting or Claude needed
```

## Install, updates and contributing

```
git clone https://github.com/attadevelops/tactician ~/Programs/tactician && ~/Programs/tactician/install.sh
```

`install.sh` links the `tactician` command, the review skill and the SwiftBar plugin, turns on the pre-push guard, makes
the recording shortcut (see Recording) and creates the tenant folder with a starter config; it never overwrites a
tenant's files. Then run `claude /tactician-setup`: it interviews you into the tenant folder (config, context map, the
access map of your work channels, denylist). Each Mac moves itself to the
newest release tag every 6 hours (and from the menu's Check for Updates), never during a call and never over local
changes. To change Tactician from any Mac: the menu's Improve Tactician... opens a Claude session in the checkout, which
files an issue or opens a pull request (`CLAUDE.md` has the rules).

## Tenant folder

Everything about one company's calls lives outside the repo, in `~/.tactician` (or `$TACTICIAN_HOME`), so the same code runs
on machines for different companies and no company's data reaches another or the repo:

| Path | What |
|---|---|
| `config.toml` | your name, project roots, default project, usual participants, Chrome profile, shortcut name (see `config.example.toml`) |
| `contexts/<root-or-project>.md` | context maps: `add-dir:` lines grant read access, `brief:` lines (`path[:max-lines]`, `{project}` allowed) feed the pre-meeting brief, every other line is prompt text (name the engagement's lanes and people here) |
| `logs/` | every run: `<run>.md` (console), `.items.jsonl` (board events), `.context.json` (the digest and brief it saw), `.judge.json` |
| `meetings/<project>/` | archived wrap-ups and action packets of live calls |
| `evals/meeting-<id>.toml` | expectations for a recorded meeting, written by the review |
| `docs/` | graded call write-ups |
| `access.md` | the access map: each work channel (chat workspace, mail account), how it is read on this Mac, what the company allows (read, react, draft), when it was verified |
| `NOTES.md` | this company's post-call steps and constraints, read by the review skill |
| `digests/<channel>.md`, `inbox.jsonl` | what the channel reader (`/loop 20m /tactician-read` in a visible session) found: a rolling digest per channel, loaded into every brief, and the direct asks of you |

With an empty tenant folder Tactician still runs: a generic prompt, no brief, projects given as paths.

## The board

A local web page (`board/index.html`, served on `--port`, default 8770, opened as a Chrome app window): a Say pane
(lines you can read aloud, sized by importance; WRONG and CAREFUL corrections in red), an Agenda pane (re-ranked as the
topic moves; raised items stay, struck through), and a "You committed" list for a read-back before the call ends.
Click any item to ask about it (or press / for a free question); Done and Wrong are your verdicts on an item, and Wrong
with a typed reason tells the model never to make that point again. A typed fact instead of a question ("the scope is
signed") settles items without an answer. End (two clicks) stops Tactician; during a call it writes the wrap-up first.
The footer shows the speech-to-board lag. The page posts with a per-run token and the server answers only to a
localhost Host, so another site in the browser cannot put words into the session.

Wording: lines are written for whoever they answer: plain words by default (a technical term at most once, in
brackets), technical only when a developer asked. Give participants with roles (`--with`, or `participants` in the
config) so Tactician can tell.

## Recording

When Tactician starts and Muesli is not recording, Tactician starts the recording, titled "<project> call", through Muesli's
"Start Meeting Recording" App Intent, run by a shortcut named `Tactician Start Meeting` (or the config's `shortcut`).
`install.sh` makes it (`tactician --make-shortcut`, then Add Shortcut in Shortcuts). Its first run asks to share the title
with Muesli: choose Always Allow, or every start waits on that prompt. Without the shortcut Tactician says so and waits for
you to start Muesli. Whenever Muesli is not transcribing, the board shows a red
"Not recording" banner (with a Start recording button when the shortcut exists), and an amber one when a recording has
sent no transcript for a minute; a question asked meanwhile is answered knowing nothing from the call has arrived.

Start Tactician a few minutes early: the session and board come up at launch, the opening agenda is ready in seconds and
questions work. The log's `meeting:` line is written when the recording starts; Ctrl-C before that archives nothing.

The call ends itself: once a call app (the browser for Meet, Zoom, Teams) has held the microphone and then let go of it for
45 seconds, Tactician runs `Tactician Stop Meeting` (Muesli's "Stop Meeting Recording", also made by `--make-shortcut`),
and Muesli's end of the recording starts the wrap-up as before. A call held in the room, with no call app, never trips it.
When the wrap-up is saved the board says so, and End just closes the window.

The meeting window is looked at every 30 seconds (and whenever the model asks): when a window titled like a call
(`Meet - `, `Zoom Meeting`, `Microsoft Teams`, or the config's `call_windows` pattern) is on screen and its picture changed,
the next transcript slice carries the image, so shared slides and documents reach the model. Only that window is captured,
never the rest of the screen; the images are kept in `logs/<run>.screens/`. It needs Screen Recording permission for the
app that starts Tactician (SwiftBar from the menu bar): without it the log says so once and the call runs on audio alone.

## What Tactician knows

- The project folder you name, plus the context map's `add-dir:` folders, read-only (Read, Grep, Glob, a graphify
  knowledge graph when `graphify-out/graph.json` exists, web search). A deny list overrides every allow rule.
- The CLAUDE.md of each readable folder (the engagement digest), put into the prompt itself.
- The pre-meeting brief: the newest wrap-up of a call in the same project root, the context map's `brief:` files, and
  what you and your Claude Code sessions in those folders said in the last 12 hours (their transcripts in
  `~/.claude/projects`, tool traffic left out).
- During the call, new lines from those sessions every 2 minutes (`[UPDATE]`), so decisions made elsewhere stop
  showing up as open items.

The session runs lean: its working directory is an empty folder under `/private/tmp/tactician/`, so the project's own
CLAUDE.md, rules, hooks and skills are not auto-loaded (tens of thousands of tokens a turn that no note used).

## After the call

The wrap-up goes to `meetings/<project>/<date>.md` and feeds the next brief; one action packet per project lane the
call touched goes next to it, written for a project's agent session to triage (handing them over is manual).
The `tactician-review` skill (`skills/tactician-review/`, link it into `~/.claude/skills`) grades the run with the LLM judge,
collects your verdicts into an eval file, folds repeated failures into the prompt behind a judged replay, and runs the
tenant's post-call steps.

## Evals

- `python3 evals/judge.py ~/.tactician/logs/<run>.md [--eval FILE] [--model sonnet] [--dry-run]`: one headless Claude
  session grades every note (verdict; correct, useful and sayable 0-2, where sayable includes the register for whoever
  it answers; importance; repeats) against the cited sources as they stood at the call, marks recall of the eval file's
  expectations by meaning, and writes `<run>.judge.json`.
- `python3 evals/score.py <log> --eval FILE`: the older regex scorer.
- Replays are pinned: a run saves the digest and brief it saw, and a replay of its log loads that snapshot (and the run's participants, unless `--with` overrides them), so it cannot
  know how the call ended. Agent-session updates replay on the transcript's own clock. Documents the session opens with
  its tools are today's versions.
- The rule for a prompt change: one judged replay per change (two when the judge and you disagree); keep it only if
  recall holds and what you flagged improved.

## Menu bar

`menubar/tactician.5s.sh` is a SwiftBar plugin (put a wrapper that `exec`s it into SwiftBar's plugin folder). The icon is
the Tactician logo (`logo.svg`): the menu bar's own colour when off, orange while waiting for Muesli, red during the call,
a yellow triangle on a problem. Start <default> Call, Start Call In (each project root, general or one of its folders,
listed live) and Start With Names... run Tactician in the background (console: `~/.tactician/logs/menubar.out`) and open the
board; every start passes the last names given (the config's `participants` at first). While it runs the menu shows the
project; Stop Now is Ctrl-C with no wrap-up. Last Wrap-up and Review Last Call (a cmux workspace running
`/tactician-review`) follow.

## Design decisions

- Capture is a tail of Muesli's database (5-15 s lag), never a second recorder.
- Silent by default: a line appears only when the project context has something the room does not.
- Output is a board of say-lines and an agenda, not explanatory notes.
- Cost levers are lean context and a 5 s poll, never a cheaper model or a classifier in front of it.
- Quality gate: the LLM judge on replays; prompt rules are principles, project facts live in the digest.
- Post-call: action packets per lane; agents triage and propose, and implement only on your go.
