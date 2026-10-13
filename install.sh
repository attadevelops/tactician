#!/bin/bash
# Install Caddie on this Mac from this checkout. Safe to run again (it never overwrites a tenant's files).
#   git clone https://github.com/attadevelops/caddie ~/Programs/caddie && ~/Programs/caddie/install.sh
set -e
C="$(cd "$(dirname "$0")" && pwd)"
T="${CADDIE_HOME:-$HOME/.caddie}"

python3 -c 'import sys, tomllib; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  || { echo "Caddie needs python3 3.11 or newer on PATH (brew install python)"; exit 1; }
git -C "$C" config core.hooksPath hooks                      # the pre-push guard against pushing tenant data

mkdir -p "$T"/{contexts,logs,meetings,evals,docs}            # the tenant folder: this company's data, never the repo's
[ -f "$T/config.toml" ] || cp "$C/config.example.toml" "$T/config.toml"
[ -f "$T/NOTES.md" ] || printf '# Tenant notes (read by the caddie-review skill)\n\nPost-call steps:\n\nConstraints:\n' > "$T/NOTES.md"
touch "$T/denylist" "$T/blocked-roots"                       # terms and private histories the pre-push guard refuses

mkdir -p "$HOME/.local/bin" "$HOME/.claude/skills"
ln -sfn "$C/caddie.py" "$HOME/.local/bin/caddie"
ln -sfn "$C/skills/caddie-review" "$HOME/.claude/skills/caddie-review"
P=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)
if [ -n "$P" ]; then
  printf '#!/bin/bash\n# <bitbar.title>Caddie</bitbar.title>\n# The plugin lives in the caddie checkout; this file only runs it.\nexec "%s/menubar/caddie.5s.sh"\n' "$C" > "$P/caddie.5s.sh"
  chmod +x "$P/caddie.5s.sh"
else
  echo "SwiftBar not found: install it (brew install --cask swiftbar) and run this again for the menu bar item"
fi

cat <<MSG
Installed. Next:
  1. Edit $T/config.toml (your name, project roots, default project, participants).
  2. Add a context map in $T/contexts/ (see README, "Tenant folder"), and this company's terms to $T/denylist.
  3. In the Shortcuts app: a new shortcut with Muesli's "Start Meeting Recording" action, its Title set to
     Shortcut Input, named "Caddie Start Meeting".
  4. Check: python3 $C/test_caddie.py
MSG
