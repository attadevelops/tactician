#!/bin/bash
# Install Tactician on this Mac from this checkout. Safe to run again (it never overwrites a tenant's files).
#   git clone https://github.com/attadevelops/tactician ~/Programs/tactician && ~/Programs/tactician/install.sh
set -e
C="$(cd "$(dirname "$0")" && pwd)"
T="${TACTICIAN_HOME:-$HOME/.tactician}"

python3 -c 'import sys, tomllib; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  || { echo "Tactician needs python3 3.11 or newer on PATH (brew install python)"; exit 1; }
git -C "$C" config core.hooksPath hooks                      # the pre-push guard against pushing tenant data

mkdir -p "$T"/{contexts,logs,meetings,evals,docs}            # the tenant folder: this company's data, never the repo's
[ -f "$T/config.toml" ] || cp "$C/config.example.toml" "$T/config.toml"
[ -f "$T/NOTES.md" ] || printf '# Tenant notes (read by the tactician-review skill)\n\nPost-call steps:\n\nConstraints:\n' > "$T/NOTES.md"
touch "$T/denylist" "$T/blocked-roots"                       # terms and private histories the pre-push guard refuses

mkdir -p "$HOME/.local/bin" "$HOME/.claude/skills"
ln -sfn "$C/tactician.py" "$HOME/.local/bin/tactician"
for s in tactician-review tactician-setup tactician-read; do ln -sfn "$C/skills/$s" "$HOME/.claude/skills/$s"; done
P=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)
if [ -n "$P" ]; then
  printf '#!/bin/bash\n# <bitbar.title>Tactician</bitbar.title>\n# The plugin lives in the tactician checkout; this file only runs it.\nexec "%s/menubar/tactician.5s.sh"\n' "$C" > "$P/tactician.5s.sh"
  chmod +x "$P/tactician.5s.sh"
else
  echo "SwiftBar not found: install it (brew install --cask swiftbar) and run this again for the menu bar item"
fi

python3 "$C/tactician.py" --make-shortcut || echo "Could not make the shortcut (needs Muesli installed): run tactician --make-shortcut later"

cat <<MSG
Installed. Next: run  claude /tactician-setup  (it interviews you into $T: config, context map, access map, denylist).
MSG
