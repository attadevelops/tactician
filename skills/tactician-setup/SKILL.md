---
name: tactician-setup
description: Set up Tactician on a new Mac, or extend this Mac's setup - interviews the user into the tenant folder (who they are, engagements, key people, managers, project folders) and writes the access map of every work channel (team chat, email, meetings) with how it is read and what is allowed. Use right after install.sh, when the user says "set up tactician", "onboard this Mac", "add an engagement", "add a channel", "update the access map", or when Tactician runs with an empty tenant.
---

# Tactician setup

Goal: one session takes this Mac from `install.sh` to a tenant Tactician can use on the next call, and an access map
that the channel readers and the work agent can rely on. The user is a consultant; this Mac works for one company.

Code: the tactician checkout this skill is linked from (`README.md`, "Tenant folder", and `config.example.toml` define
the formats). Data: the tenant folder `~/.tactician` (or `$TACTICIAN_HOME`). Safe to run again: it extends what is there.

## Discover before asking

Find everything the Mac can tell you, then ask only for the rest. Useful signals:
- Who: `git config user.name`, the macOS full name, the tenant's current `config.toml`.
- Project folders: folders under `~` holding several git repos or a CLAUDE.md, and the folders the user's Claude Code
  sessions run in (`~/.claude/projects` directory names are the paths with `/` turned into `-`).
- Existing engagement digests: a CLAUDE.md at a project root, skills under `~/.claude/skills` that hold company facts.
- Channels: chat and mail apps in `/Applications` (Slack, Microsoft Teams, Outlook, Mail), Chrome profiles
  (`~/Library/Application Support/Google/Chrome/Local State`, `profile.info_cache`: profile directory, name, account),
  a Chrome listening for CDP (`lsof -nP -iTCP -sTCP:LISTEN | grep -i chrome`) and its `--user-data-dir`, mail accounts.
- Prerequisites: Muesli installed, `claude` signed in, `gh auth status` on the personal account, SwiftBar, the
  recording shortcut (`shortcuts list`).

Report what you found in one short block, then interview.

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
- The menu bar shows the default project; the recording shortcut exists. When `participants` changed, remove
  `logs/menubar.participants` (the menu bar's last typed names, which override the config default).

## Close

- Summarize what was written and what is still "not yet", in a few lines.
- File rough edges from this run as issues in the tactician repo (`gh issue create`, personal account), in generic words:
  no company, client or people names in an issue, ever.
