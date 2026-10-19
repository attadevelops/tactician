---
name: tactician-setup
description: Install and set up Tactician on a new Mac, or extend or repair this Mac's setup - makes every prerequisite true (SwiftBar, both recording shortcuts, permissions), interviews the user into the tenant folder (who they are, engagements, key people, managers, project folders), writes the access map of every work channel, and proves the whole thing works. Use right after cloning the repo, when the user says "install tactician", "set up tactician", "onboard this Mac", "check my setup", "add an engagement", "add a channel", "update the access map", or when Tactician runs with an empty tenant.
---

# Tactician setup

Goal: one session takes this Mac from a fresh clone to a tenant Tactician can use on the next call, proven by tests,
and an access map that the channel readers and the work agent can rely on. The user is a consultant; this Mac works for
one company.

Code: the tactician checkout this skill is loaded from (`README.md`, "Tenant folder", and `config.example.toml` define
the formats). Data: the tenant folder `~/.tactician` (or `$TACTICIAN_HOME`). Safe to run again: it checks every item,
fixes only what is not true, and extends the tenant rather than replacing it.

## The Mac is ready when all of these hold

Check each one first, fix what fails, then check it again; finish with this list ticked in your report. Install with
Homebrew when it is there (and ask before installing Homebrew itself). The user does what only a person can (a click in
Shortcuts or System Settings, a sign-in): open the exact window for them, say the one click, then verify it yourself.
A company-managed Mac may block an install: then say what is blocked and carry on with the rest.
- `python3` is 3.11 or newer; Muesli is in `/Applications`; `claude` is signed in.
- SwiftBar is installed, has a plugin folder (`defaults read com.ameba.SwiftBar PluginDirectory`; when unset, write
  `~/.config/swiftbar-plugins` there before SwiftBar's first launch), and is running.
- `install.sh` from the checkout has run (it links the command, the skills and the menu bar plugin, makes the tenant
  folder, and writes both shortcut files). It is plumbing and safe to re-run.
- Both shortcuts are in `shortcuts list` (the config's `shortcut` and `stop_shortcut`, default "Tactician Start Meeting"
  and "Tactician Stop Meeting"). `install.sh` opens Shortcuts on each signed file: the user clicks Add Shortcut. Run the
  stop shortcut once (harmless when nothing records) so its Always Allow prompt for Muesli is answered now, not mid-call.
  Never run the start shortcut in a test: it starts a real recording.
  When signing fails (no iCloud, common on a company-managed Mac), build both in the Shortcuts app by UI scripting
  (System Events): a new shortcut, its name, the Muesli action found by search and added with Return. The start one's
  Title cannot be bound to Shortcut Input this way, so it gets a fixed title (Tactician finds the recording by status).
  The first run's "Allow ... to output 1 boolean?" is in no accessibility tree: it needs a real mouse click (a CGEvent
  at the button), or the user's click.
- The checkout commits as the repo owner's personal identity: `git -C <checkout> config user.email` is in
  `hooks/authors`, never the company account (set it repo-local; the pre-push guard refuses anything else).
- SwiftBar has Screen Recording (it reads call window titles): open
  `x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture`, the user turns SwiftBar on.
- The Tactician icon is visible in the menu bar, not hidden under the notch: when it is missing, set
  `"NSStatusItem Preferred Position tactician.5s.sh"` in SwiftBar's defaults low enough to land left of the notch,
  restart SwiftBar, and ask the user to confirm they see it.
- The menu lists the right folders: after the tenant is written, run the plugin
  (`<checkout>/menubar/tactician.5s.sh`) and confirm its output names every project folder the user takes calls in,
  including the folders an existing assistant system keeps (they become `project_roots`).

## Discover before asking

Find everything the Mac can tell you, then ask only for the rest. Useful signals:
- Who: `git config user.name`, the macOS full name, the tenant's current `config.toml`.
- Project folders: folders under `~` holding several git repos or a CLAUDE.md, and the folders the user's Claude Code
  sessions run in (`~/.claude/projects` directory names are the paths with `/` turned into `-`).
- Existing engagement digests: a CLAUDE.md at a project root, skills under `~/.claude/skills` that hold company facts.
- Channels: chat and mail apps in `/Applications` (Slack, Microsoft Teams, Outlook, Mail), Chrome profiles
  (`~/Library/Application Support/Google/Chrome/Local State`, `profile.info_cache`: profile directory, name, account),
  a Chrome listening for CDP (`lsof -nP -iTCP -sTCP:LISTEN | grep -i chrome`) and its `--user-data-dir`, mail accounts.

- An assistant system already running here (the user's own notes vault, digest or chat-watcher): folders with a
  CLAUDE.md or memory files outside the project roots, `~/Library/LaunchAgents` and `crontab -l` entries that run
  `claude` or scripts, skills under `~/.claude/skills` that read chat or mail. Ask the user where theirs lives.

Report what you found in one short block, then interview.

## Reconcile an existing system

Tactician joins what already works on this Mac; it never replaces it. When discovery finds one:
- Inventory each part: what it reads, what it writes and where, its schedule, and whether it works (its logs show
  recent good runs; the user says what they rely on).
- Map each part to Tactician: its knowledge store becomes the engagement digest (an `add-dir:` or `brief:` line, the
  files stay where they are); its chat or mail reader becomes that channel's reader (record it in `access.md` as the
  `read` method; never add a second reader of the same channel); its write rules become that channel's `allowed`.
- Never move, delete, disable or rewrite its files, jobs or data. A change to it needs the user's yes, one at a time.
- Write the map in `NOTES.md` under "Existing system": each part, its Tactician role, and what stays its own.
- What it does that Tactician lacks, or does better: file an issue per behaviour (generic words) so it reaches every
  Mac through a release, then switch this Mac over only after the release matches it.

## Interview

AskUserQuestion, a few batched questions at a time, each with the discovered value as the recommended option. Cover:
- **The user**: name as it appears in transcripts, role at this company.
- **Engagements**: each one's name, its project folders and lanes, what is in and out of scope, where its contract or
  SOW and its digest live (paths, not contents).
- **People**: for each engagement and company-wide: name, role, technical or not, who runs the calls, who decides, who
  the user reports to. Managers who get progress reports, how often, through which channel.
- **Channels** (the access map, below): for each one, the purpose, how it is read on this Mac, and what is allowed.
- **Company rules**: what the employer forbids (connecting chat or mail to third-party apps, recording, AI on client
  data), and terms that must never leave this Mac.

Never ask what discovery already answered; confirm it instead.

## Write the tenant

Show each file's new content (or the diff for an existing file) and get one approval before writing. Never overwrite
the user's edits silently.
- `config.toml`: `user`, `project_roots`, `default_project`, `participants` (the usual call, with roles),
  `browser_profile`, `shortcut`. Format: `config.example.toml`.
- `contexts/<root-or-project>.md`: `add-dir:` for each readable folder (project root, digests, memory folders),
  `brief:` for files the pre-meeting brief should load, then prompt text: the engagement, its lanes (name packet lanes
  by repo), and one Register line saying who is technical and who is not. Facts that change weekly belong in the
  engagement digest, not here.
- `access.md`: the access map (below).
- `NOTES.md`: post-call steps (where wrap-ups and commitments go) and Constraints (the company rules, read-only paths).
- `denylist`: one term per line, the company, client and people names and any code names, so the pre-push guard stops
  them reaching the repo.

### The access map (`access.md`)

One section per channel, a channel being one workspace or account (two Slack workspaces are two channels):

```
## <channel name>
- purpose: <what arrives here: client asks, team decisions, manager updates>
- engagements: <which ones>
- read: <method, exact profile or app>   e.g. CDP on Chrome --user-data-dir=<dir>, Slack web tab in Chrome profile <dir>
- read state: <does reading mark messages read; how it was measured, or "unmeasured">
- allowed: <read | react (which emoji) | draft | never post>   the employer's rule, not the technical limit
- direct asks: <what counts: DMs, @mentions, threads the user is in>
- verified: <date and how, or "not yet">
```

Rules: reading goes through the user's own logged-in UI unless the employer allows a connection; never store a
session token, cookie or password in the tenant; `allowed` defaults to read and draft only, and `react` needs the
user's explicit yes per channel.

## Verify

- `python3 <checkout>/test_tactician.py` passes, and `tactician --shell-config` prints this tenant.
- Each channel marked readable: read the newest few messages once by the stated method, without opening unread
  conversations where avoidable, and record the result in its `verified` line. A method that fails stays "not yet".
  Measure read state: the unread count before and after. In a background tab, `get_page_text` and `innerText` can come
  back empty: read list rows with `textContent`, and note the working selector in the `read` line.
- Every item under "The Mac is ready when" holds. When `participants` changed, remove `logs/menubar.participants`
  (the menu bar's last typed names, which override the config default).
- End to end: when Muesli has a finished meeting, replay a few minutes of it in the background
  (`tactician <project> --replay <id> --from <hh:mm:ss> --to <hh:mm:ss> --speed 20`) and confirm the log shows the
  session answering and board items appearing; then close the board.

## Close

- Summarize what was written and what is still "not yet", in a few lines.
- File rough edges from this run as issues in the tactician repo (`gh issue create`, personal account), in generic words:
  no company, client or people names in an issue, ever.
