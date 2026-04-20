#!/bin/bash
# tinker — statusline badge script for Claude Code
# Reads the tinker mode flag file and outputs a colored badge.
#
# Usage in ~/.claude/settings.json:
#   "statusLine": { "type": "command", "command": "bash /path/to/tinker-statusline.sh" }
#
# Plugin users: Claude will offer to set this up on first session.
# Standalone users: install.sh wires this automatically.

FLAG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.tinker-active"

# Refuse symlinks — a local attacker could point the flag at ~/.ssh/id_rsa and
# have the statusline render its bytes (including ANSI escape sequences) to
# the terminal every keystroke.
[ -L "$FLAG" ] && exit 0
[ ! -f "$FLAG" ] && exit 0

# Hard-cap the read at 64 bytes and strip anything outside [a-z0-9-] — blocks
# terminal-escape injection and OSC hyperlink spoofing via the flag contents.
MODE=$(head -c 64 "$FLAG" 2>/dev/null | tr -d '\n\r' | tr '[:upper:]' '[:lower:]')
MODE=$(printf '%s' "$MODE" | tr -cd 'a-z0-9-')

# Whitelist. Anything else → render nothing rather than echo attacker bytes.
case "$MODE" in
  off|lite|full|ultra|commit|review|compress|tinker|tinker-lite|tinker-ultra|tinker-compressed) ;;
  *) exit 0 ;;
esac

if [[ "$MODE" == tinker* ]]; then
  # Green for Tinker (Mad scientist vibe)
  if [ "$MODE" = "tinker" ]; then
    printf '\033[38;5;34m[TINKER]\033[0m'
  else
    SUFFIX=$(printf '%s' "$MODE" | sed 's/tinker-//' | tr '[:lower:]' '[:upper:]')
    printf '\033[38;5;34m[TINKER:%s]\033[0m' "$SUFFIX"
  fi
elif [ -z "$MODE" ] || [ "$MODE" = "full" ]; then
  # Orange for Mad Tinker
  printf '\033[38;5;172m[TINKER]\033[0m'
else
  SUFFIX=$(printf '%s' "$MODE" | tr '[:lower:]' '[:upper:]')
  printf '\033[38;5;172m[TINKER:%s]\033[0m' "$SUFFIX"
fi
