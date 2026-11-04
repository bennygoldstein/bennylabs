#!/bin/sh
# Taskbar Sliders installer for macOS and Linux. Run it with:  sh install.sh
#
# What it does, in order:
#   1. Works out if this is macOS or Linux (anything else: STATUS: FAILED).
#   2. Finds a python3 that has tkinter. If there is none:
#        macOS: tries "brew install python-tk" when Homebrew is there.
#        Linux: never uses sudo; it prints the one command you should run.
#      Still none: STATUS: NEED_PYTHON and exit 1.
#   3. Downloads latest.json, takes the entry for this system, downloads the
#      program and checks its sha256 (and that it compiles). Else STATUS: FAILED.
#   4. Puts it in  ~/Library/Application Support/ScreenSliders  (macOS)
#      or  ~/.local/share/ScreenSliders  (Linux). Same version and running:
#      STATUS: UP_TO_DATE. Older version: it is backed up beside the new one,
#      settings are kept.
#   5. Sets it to start at login (macOS LaunchAgent, Linux autostart file),
#      starts it, waits 5 seconds and checks it is still running.
# The first line printed is always one of:
#   STATUS: INSTALLED / UPDATED / UP_TO_DATE / NEED_PYTHON / FAILED

MANIFEST_URL="${SLIDERS_MANIFEST:-https://bennylabs.org/my-prompts/sliders/latest.json}"
PATTERN='ScreenSliders/sliders_unix\.py$'

case "$(uname -s)" in
  Darwin)
    KEY=mac
    DIR="$HOME/Library/Application Support/ScreenSliders"
    PLIST="$HOME/Library/LaunchAgents/org.bennylabs.screensliders.plist"
    ;;
  Linux)
    KEY=linux
    DIR="$HOME/.local/share/ScreenSliders"
    DESK="$HOME/.config/autostart/screensliders.desktop"
    ;;
  *)
    echo "STATUS: FAILED"
    echo "This installer only works on macOS and Linux."
    exit 1
    ;;
esac

fail() {
  echo "STATUS: FAILED"
  echo "$1"
  if [ -f "$DIR/sliders.log" ]; then
    echo "--- end of sliders.log"
    tail -n 15 "$DIR/sliders.log"
  fi
  exit 1
}

# ---- 2. python3 with tkinter
PY=""
find_py() {
  for p in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -I -c 'import tkinter' >/dev/null 2>&1; then
      PY=$(command -v "$p")
      return 0
    fi
  done
  return 1
}

if ! find_py; then
  if [ "$KEY" = mac ] && command -v brew >/dev/null 2>&1; then
    brew install python-tk >/dev/null 2>&1
    find_py
  fi
fi
if [ -z "$PY" ]; then
  echo "STATUS: NEED_PYTHON"
  if [ "$KEY" = mac ]; then
    if command -v brew >/dev/null 2>&1; then
      echo "Run:  brew install python-tk   then run this installer again."
    else
      echo "Install Python 3 (it includes tkinter) from https://www.python.org/downloads/macos/ then run this installer again."
    fi
  else
    ID_ALL=""
    [ -r /etc/os-release ] && ID_ALL=$(sed -n 's/^ID=//p;s/^ID_LIKE=//p' /etc/os-release | tr -d '"' | tr '\n' ' ')
    case " $ID_ALL " in
      *debian*|*ubuntu*) CMD="sudo apt install python3-tk" ;;
      *fedora*|*rhel*|*centos*) CMD="sudo dnf install python3-tkinter" ;;
      *arch*) CMD="sudo pacman -S tk" ;;
      *) CMD="install the tkinter package for python3 with your package manager (apt: python3-tk, dnf: python3-tkinter, pacman: tk)" ;;
    esac
    echo "Run:  $CMD   then run this installer again."
  fi
  exit 1
fi

# ---- 3. download and check
TMP=$(mktemp -d 2>/dev/null) || fail "Could not make a temporary folder."
trap 'rm -rf "$TMP"' EXIT

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 60 -o "$2" "$1"
  else
    "$PY" -I -c 'import sys,urllib.request; open(sys.argv[2],"wb").write(urllib.request.urlopen(sys.argv[1],timeout=60).read())' "$1" "$2"
  fi
}

fetch "$MANIFEST_URL" "$TMP/latest.json" >/dev/null 2>&1 || fail "Could not download $MANIFEST_URL"
set -- $("$PY" -I -c 'import json,sys; e=json.load(open(sys.argv[1]))["files"][sys.argv[2]]; print(int(e["version"]), e["url"], e["sha256"])' "$TMP/latest.json" "$KEY" 2>/dev/null)
NEWV="$1"; URL="$2"; SHA="$3"
[ -n "$NEWV" ] && [ -n "$URL" ] && [ -n "$SHA" ] || fail "latest.json has no usable entry for $KEY."
fetch "$URL" "$TMP/prog.py" >/dev/null 2>&1 || fail "Could not download $URL"
GOT=$("$PY" -I -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP/prog.py" 2>/dev/null)
WANT=$(echo "$SHA" | tr 'A-F' 'a-f')
[ "$GOT" = "$WANT" ] || fail "The download does not match its sha256. Nothing was installed."
"$PY" -I -c 'import sys; compile(open(sys.argv[1],encoding="utf-8").read(),"prog","exec")' "$TMP/prog.py" >/dev/null 2>&1 || fail "The downloaded program does not compile. Nothing was installed."

# ---- 4. put it in place
RUNNING=0
pgrep -f "$PATTERN" >/dev/null 2>&1 && RUNNING=1
OLDV=""
if [ -f "$DIR/sliders_unix.py" ]; then
  OLDV=$(sed -n 's/^VERSION = "\([0-9]*\)".*/\1/p' "$DIR/sliders_unix.py" | head -n 1)
fi
if [ -n "$OLDV" ] && [ "$OLDV" = "$NEWV" ] && [ "$RUNNING" = 1 ]; then
  echo "STATUS: UP_TO_DATE"
  echo "Version $NEWV is installed and running."
  exit 0
fi
STATUS=INSTALLED
mkdir -p "$DIR" || fail "Could not create $DIR"
if [ -f "$DIR/sliders_unix.py" ]; then
  [ -n "$OLDV" ] && [ "$OLDV" != "$NEWV" ] && STATUS=UPDATED
  B="$DIR/sliders_unix_v${OLDV:-old}.py"
  n=2
  while [ -e "$B" ]; do B="$DIR/sliders_unix_v${OLDV:-old}_$n.py"; n=$((n + 1)); done
  cp "$DIR/sliders_unix.py" "$B"
fi
[ "$RUNNING" = 1 ] && pkill -f "$PATTERN" >/dev/null 2>&1 && sleep 1
cp "$TMP/prog.py" "$DIR/sliders_unix.py" || fail "Could not write the program."

# ---- 5. start at login, start now
if [ "$KEY" = mac ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>org.bennylabs.screensliders</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$DIR/sliders_unix.py</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
EOF
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1
  launchctl bootstrap "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || launchctl load -w "$PLIST" >/dev/null 2>&1
  sleep 1
else
  mkdir -p "$HOME/.config/autostart"
  cat > "$DESK" <<EOF
[Desktop Entry]
Type=Application
Name=Screen Sliders
Exec="$PY" "$DIR/sliders_unix.py"
X-GNOME-Autostart-enabled=true
EOF
fi
# the program allows only one copy, so a second start is harmless
pgrep -f "$PATTERN" >/dev/null 2>&1 || nohup "$PY" "$DIR/sliders_unix.py" >/dev/null 2>&1 &
sleep 5
pgrep -f "$PATTERN" >/dev/null 2>&1 || fail "The program started but did not stay running."

echo "STATUS: $STATUS"
echo "Version $NEWV is running. Installed in: $DIR"
echo "To stop it:    pkill -f 'ScreenSliders/sliders_unix\\.py\$'"
if [ "$KEY" = mac ]; then
  echo "To remove it:  launchctl bootout gui/\$(id -u) \"$PLIST\"; rm \"$PLIST\"; rm -r \"$DIR\""
else
  echo "To remove it:  rm \"$DESK\"; rm -r \"$DIR\""
fi
