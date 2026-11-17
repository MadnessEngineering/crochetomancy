"""Where the prompt hook keeps per-session data (prompts, agent name).

These files used to go to a cwd-relative .claude/data/sessions/, which
scattered them, full prompt text included, into whatever repo a session ran
in (build/ and public/ folders too). They now live in one place outside
every repo.
"""

import re
from pathlib import Path

SESSIONS_DIR = Path.home() / ".cache" / "claude-hooks" / "sessions"
_LEGACY_DIR = Path(".claude") / "data" / "sessions"


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")


def session_file(session_id: str) -> Path:
    return SESSIONS_DIR / f"{_safe(session_id)}.json"


def existing_session_file(session_id: str) -> Path | None:
    """The session's file, falling back to the legacy cwd-relative location
    so sessions started before the move still resolve."""
    for candidate in (session_file(session_id), _LEGACY_DIR / f"{_safe(session_id)}.json"):
        if candidate.exists():
            return candidate
    return None
