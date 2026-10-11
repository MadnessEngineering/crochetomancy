"""Auto-color Claude Code sessions by project.

Claude Code has no hook output field for the prompt-bar color (only
`sessionTitle`). This mimics what `/color` does under the hood
(Claude Code 2.1.284, undocumented — may break on update, so every path
fails silent):

  1. Background/fleet sessions own `~/.claude/jobs/<sid[:8]>/state.json`.
     Claude Code fs.watches it and applies `color` live when the session
     has none yet.
  2. Every session's transcript gets `{"type":"agent-color",...}`, which
     Claude Code restores on `/resume`. Foreground sessions have no job
     dir, so this is their only path — color shows after resume, not live.

A user `/color` always wins: we never overwrite an existing state.json
color, and only stamp the transcript once per session (marker file).

Overrides: `~/.claude/session_colors.json` → {"<project>": "<color>"}.
Unmapped projects get a stable hash-picked color.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

COLORS = ["red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"]

DEFAULT_PROJECT_COLORS = {
    "madness_interactive": "orange",
    "inventorium": "purple",
    "omnispindle": "cyan",
    "cartogomancy": "green",
    "anathesmelt": "red",
    "madnessdesktop": "yellow",
}

CLAUDE_DIR = Path.home() / ".claude"
OVERRIDES_FILE = CLAUDE_DIR / "session_colors.json"
MARKER_DIR = CLAUDE_DIR / "data" / "session-colors"


def detect_project(cwd):
    """Git toplevel basename (submodules resolve to themselves), else cwd basename."""
    try:
        top = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if top:
            return Path(top).name
    except Exception:
        pass
    return Path(cwd).name


def color_for_project(project):
    key = project.lower()
    try:
        overrides = {k.lower(): v for k, v in json.loads(OVERRIDES_FILE.read_text()).items()}
    except Exception:
        overrides = {}
    color = overrides.get(key) or DEFAULT_PROJECT_COLORS.get(key)
    if color in COLORS:
        return color
    digest = hashlib.sha1(key.encode()).digest()
    return COLORS[digest[0] % len(COLORS)]


def _stamp_job_state(session_id, color):
    """Live path: only for sessions with a job dir, and only if no color yet."""
    state_path = CLAUDE_DIR / "jobs" / session_id[:8] / "state.json"
    if not state_path.exists():
        return False
    state = json.loads(state_path.read_text())
    if state.get("color"):
        return False
    state["color"] = color
    tmp = state_path.with_name("state.json.color-tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, state_path)
    return True


def _stamp_transcript(session_id, transcript_path, color):
    """Resume path: append once per session, and only once the transcript exists."""
    if not transcript_path or not Path(transcript_path).exists():
        return False
    marker = MARKER_DIR / session_id
    if marker.exists():
        return False
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    if '"type":"agent-color"' in Path(transcript_path).read_text(errors="ignore"):
        marker.write_text("user")  # a /color already landed — leave it be
        return False
    with open(transcript_path, "a") as f:
        f.write(json.dumps({"type": "agent-color", "agentColor": color, "sessionId": session_id}, separators=(",", ":")) + "\n")
    marker.write_text(color)
    return True


def apply_session_color(input_data):
    """Idempotent — safe to call from SessionStart and every UserPromptSubmit."""
    try:
        session_id = input_data.get("session_id")
        if not session_id:
            return None
        color = color_for_project(detect_project(input_data.get("cwd") or os.getcwd()))
        try:
            _stamp_job_state(session_id, color)
        except Exception:
            pass
        try:
            _stamp_transcript(session_id, input_data.get("transcript_path"), color)
        except Exception:
            pass
        return color
    except Exception:
        return None
