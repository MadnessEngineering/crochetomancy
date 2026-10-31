"""Open background forks in their own Warp window, already attached.

`/fork` (and any other fork) spawns a background session; reaching it
normally means a new pane → `claude agents` → hunt the list. On
SessionStart with source "fork" we instead write a Warp launch
configuration that runs `claude attach <id8>` and open it via
`warp://launch/<file>`.

Warp quirks (verified against Warp v0.2025.10.29):
  - The URI path must be a file name relative to
    `~/.warp/launch_configurations/`. Absolute paths are rejected
    ("couldn't turn launch link into path") and subfolders don't launch,
    so configs live top-level as `claude-fork-<id8>.yaml` and get pruned.
  - The config's `name:` must equal the file stem, or Warp logs
    "couldn't find a matching file path" (v0.2026.10.07; every fork
    before this fix failed that way).
  - A freshly written config only resolves once Warp's watcher indexes
    it, so the open runs from a detached `sleep 3`.
  - The window opens without stealing focus from a quake-mode terminal.

Skips silently unless: macOS, Warp installed, source == "fork", and the
session is a background job (has `<config dir>/jobs/<id8>/`) — a foreground
fork is already in front of you. The config dir follows CLAUDE_CONFIG_DIR, so
a fork from a profile alias (`emclau`) attaches back into that profile.

Shellomancy (our Warp fork) first: when a Shellomancy app with local control is
running, the fork opens as a new TAB there via its local-control API
(`shellomancy_client.open_tab`), no launch config or watcher delay. If it isn't
running or anything fails, the Warp window path above runs instead. Disable with
`"fork_shellomancy": false` in hooks_config.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from utils.session_color import CLAUDE_DIR, color_for_project, detect_project

LAUNCH_DIR = Path.home() / ".warp" / "launch_configurations"
CONFIG_PREFIX = "claude-fork-"
PRUNE_AFTER_SECONDS = 24 * 3600
OPEN_DELAY_SECONDS = 3
WARP_APP_GLOBS = ("/Applications/Warp*.app", str(Path.home() / "Applications" / "Warp*.app"))
SHELLOMANCY_CLIENT_DIR = (
    Path(os.environ.get("SEAT_OF_MADNESS", Path.home() / "lab" / "madness_interactive"))
    / "projects" / "common" / "shellomancy" / "script" / "shellomancy_client"
)
# Session ids are UUIDs; the id8 goes into a shell command, so accept hex only.
SHORT_ID_RE = re.compile(r"[0-9a-f]{8}")

# Warp tab colors are ANSI-named; map Claude Code's /color palette onto them.
WARP_TAB_COLORS = {
    "red": "red", "blue": "blue", "green": "green", "yellow": "yellow",
    "purple": "magenta", "pink": "magenta", "orange": "yellow", "cyan": "cyan",
}


def warp_installed():
    for pattern in WARP_APP_GLOBS:
        root, _, glob = pattern.rpartition("/")
        if any(Path(root).glob(glob)):
            return True
    return False


def _prune_old_configs():
    cutoff = time.time() - PRUNE_AFTER_SECONDS
    for path in LAUNCH_DIR.glob(f"{CONFIG_PREFIX}*.yaml"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except Exception:
            pass


def _attach_command(short_id):
    claude = shutil.which("claude") or "claude"
    attach = f"{shlex.quote(claude)} attach {short_id}"
    # Non-default profile (e.g. `emclau`): attach through the same config dir,
    # or the fork is invisible to the default daemon.
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        attach = f"CLAUDE_CONFIG_DIR={shlex.quote(config_dir)} {attach}"
    return attach


def _shellomancy_client():
    """Import the Shellomancy client from the lab checkout, or None."""
    if not (SHELLOMANCY_CLIENT_DIR / "shellomancy_client.py").is_file():
        return None
    if str(SHELLOMANCY_CLIENT_DIR) not in sys.path:
        sys.path.insert(0, str(SHELLOMANCY_CLIENT_DIR))
    try:
        import shellomancy_client
    except Exception:
        return None
    return shellomancy_client


def open_fork_in_shellomancy(short_id, cwd, project, color):
    """Open the fork as a Shellomancy tab. True only if the app accepted it."""
    client = _shellomancy_client()
    if client is None:
        return False
    try:
        if not client.list_instances():
            return False
        client.open_tab(
            command=_attach_command(short_id),
            cwd=str(cwd),
            title=f"⑂ {project} {short_id}",
            color=WARP_TAB_COLORS.get(color, "blue"),
        )
        return True
    except Exception:
        return False


def _write_launch_config(short_id, cwd, project, color):
    # JSON strings are valid YAML scalars — sidesteps quoting paths/titles by hand.
    def q(s): return json.dumps(s, ensure_ascii=False)
    attach = _attach_command(short_id)
    stem = f"{CONFIG_PREFIX}{short_id}"
    lines = [
        "---",
        # Warp resolves warp://launch/<file> by config name — must equal the file stem.
        f"name: {stem}",
        "windows:",
        "  - tabs:",
        f"      - title: {q(f'⑂ {project} {short_id}')}",
        f"        color: {WARP_TAB_COLORS.get(color, 'blue')}",
        "        layout:",
        f"          cwd: {q(str(cwd))}",
        "          commands:",
        f"            - exec: {q(attach)}",
    ]
    path = LAUNCH_DIR / f"{stem}.yaml"
    path.write_text("\n".join(lines) + "\n")
    return path


def open_fork_window(input_data, use_shellomancy=True):
    """Returns "shellomancy" when a Shellomancy tab opened, the launch config
    path when a Warp window was requested, else None."""
    try:
        if sys.platform != "darwin" or input_data.get("source") != "fork":
            return None
        session_id = input_data.get("session_id") or ""
        short_id = session_id[:8]
        if not SHORT_ID_RE.fullmatch(short_id) or not (CLAUDE_DIR / "jobs" / short_id).is_dir():
            return None
        cwd = input_data.get("cwd") or str(Path.cwd())
        project = detect_project(cwd)
        color = color_for_project(project)
        if use_shellomancy and open_fork_in_shellomancy(short_id, cwd, project, color):
            return "shellomancy"
        if not warp_installed():
            return None
        LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
        _prune_old_configs()
        path = _write_launch_config(short_id, cwd, project, color)
        # Detached delay: a brand-new config isn't resolvable until Warp's
        # file watcher indexes it (immediate opens silently no-op ~half the time).
        subprocess.Popen(
            ["sh", "-c", f"sleep {OPEN_DELAY_SECONDS}; open 'warp://launch/{path.name}'"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return path
    except Exception:
        return None
