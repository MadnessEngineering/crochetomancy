"""Remembers which todos a session has already been shown.

Context injection runs on every prompt. Without a memory the same block is
re-sent each time, so this keeps, per session and project, the status and
description of every todo already injected. The prompt hook then sends only
what is new or changed. SessionStart clears it after a compaction, because
the summary no longer carries the earlier blocks.

Files live outside every repo, in ~/.cache/claude-hooks/injected/.
"""

import json
import re
import time
from pathlib import Path

_DIR = Path.home() / ".cache" / "claude-hooks" / "injected"
_MAX_AGE_SECONDS = 14 * 24 * 3600


def _path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return _DIR / f"{safe}.json"


def load_shown(session_id: str, project: str) -> dict:
    """{todo_id: {"status": ..., "description": ...}} already shown for this project."""
    try:
        data = json.loads(_path(session_id).read_text())
        return data.get(project, {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_shown(session_id: str, project: str, shown: dict) -> None:
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        path = _path(session_id)
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[project] = shown
        path.write_text(json.dumps(data))
        _prune_old()
    except Exception:
        pass  # memory is an optimisation; never block the prompt


def clear_shown(session_id: str) -> None:
    try:
        _path(session_id).unlink(missing_ok=True)
    except Exception:
        pass


def _prune_old() -> None:
    cutoff = time.time() - _MAX_AGE_SECONDS
    for entry in _DIR.glob("*.json"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except Exception:
            pass
