#!/bin/bash
# <bitbar.title>Tactician</bitbar.title>
# <bitbar.desc>Start, watch and stop Tactician, the live meeting prompter.</bitbar.desc>
# Thin menu-bar front end for menubar/tactician-menu (a wrapper in ~/.config/swiftbar-plugins runs this file).
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"  # SwiftBar's PATH is bare; tactician.py needs a python3 with tomllib
C="$(cd "$(dirname "$0")/.." && pwd)"
eval "$(python3 "$C/tactician.py" --shell-config)"  # TACTICIAN_HOME, DEFAULT_PROJECT, PARTICIPANTS, PROFILE, ROOTS
OUT="$TACTICIAN_HOME/logs/menubar.out"; PID="$TACTICIAN_HOME/logs/menubar.pid"
RUN="bash=$C/menubar/tactician-menu terminal=false refresh=true"
stamp="$TACTICIAN_HOME/logs/.update-check"  # look for a new release every 6 hours, in the background
if [ -d "$TACTICIAN_HOME/logs" ] && [ -z "$(find "$stamp" -mmin -360 2> /dev/null)" ]; then
  touch "$stamp"; nohup "$C/menubar/tactician-menu" update > /dev/null 2>&1 &
fi
logo() { sed "s/#000/$1/g" "$C/logo.svg" | base64; }  # SwiftBar decodes the SVG natively; $1 recolors it

if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  if grep -q -e "turn error" -e "claude exited" "$OUT"; then
    echo " | sfimage=exclamationmark.triangle.fill sfcolor=systemYellow"; state="Problem: see Console"
  elif grep -q "recording detected" "$OUT"; then
    echo " | image=$(logo '#FF3B30')"; state="Live: following the call"
  else
    echo " | image=$(logo '#FF9500')"; state="Ready: waiting for Muesli to record"
  fi
  proj=$(grep -m1 -o 'Tactician for [^.]*' "$OUT" | cut -c12-)
  echo "---"
  echo "$state | size=13"
  echo "Project: $proj | size=12"
  echo "Open Board | $RUN param1=board sfimage=rectangle.split.2x1"
  echo "Stop and Wrap Up | $RUN param1=stop sfimage=stop.circle"
else
  echo " | templateImage=$(logo '#000')"
  echo "---"
  [ -n "$DEFAULT_PROJECT" ] && echo "Start $DEFAULT_PROJECT Call | $RUN param1=start sfimage=play.circle"
  echo "Start Call In | sfimage=folder"
  for r in "${ROOTS[@]}"; do  # each project root, then its folders, listed live so a new repo shows up by itself
    echo "--$(basename "$r"), general (any project) | $RUN param1=start param2=$r"
    for d in "$r"/*/; do
      n=$(basename "$d"); case "$n" in _*) continue ;; esac
      echo "--$n | $RUN param1=start param2=$d"
    done
  done
  echo "Start With Names… | $RUN param1=start-with sfimage=person.2"
fi
echo "---"
echo "Console | bash=/usr/bin/open param1=-e param2=$OUT terminal=false sfimage=terminal"
echo "Last Wrap-up | $RUN param1=wrapup sfimage=doc.text"
echo "Review Last Call | $RUN param1=review sfimage=checklist"
echo "---"
echo "Tactician $(git -C "$C" describe --tags --always 2> /dev/null) | size=11"
echo "Check for Updates | $RUN param1=update sfimage=arrow.down.circle"
echo "Improve Tactician… | $RUN param1=improve sfimage=hammer"
