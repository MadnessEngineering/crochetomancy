"""Claude Code usage stats fetcher.

Mirrors gxjansen/claude-code-meter behavior: pull oauth token from macOS keychain,
hit /api/oauth/usage, return parsed 5h + 7d windows. Caches /tmp/claude-usage-cache.json
and applies exponential backoff on 429 so hooks never spam the endpoint.

Used inline by session_end.py and post_compact.py to enrich their MQTT payloads.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional

CACHE_FILE = Path("/tmp/claude-usage-cache.json")
BACKOFF_FILE = Path("/tmp/claude-usage-backoff")
BACKOFF_WAIT_FILE = Path("/tmp/claude-usage-backoff.wait")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.1.0"
ANTHROPIC_BETA = "oauth-2025-04-20"
TIMEOUT_S = 4


def _keychain_token() -> Optional[dict]:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return json.loads(out.stdout.strip()).get("claudeAiOauth")
    except Exception:
        return None


def _read_cache() -> Optional[dict]:
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return None


def _write_cache(body: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(body))
    except Exception:
        pass


def _in_backoff() -> bool:
    try:
        until = int(BACKOFF_FILE.read_text().strip())
        return time.time() < until
    except Exception:
        return False


def _set_backoff() -> None:
    try:
        prev = int(BACKOFF_WAIT_FILE.read_text().strip()) if BACKOFF_WAIT_FILE.exists() else 0
    except Exception:
        prev = 0
    if prev < 300:
        nxt = 300
    elif prev < 900:
        nxt = min(prev * 2, 900)
    else:
        nxt = 900
    try:
        BACKOFF_FILE.write_text(str(int(time.time() + nxt)))
        BACKOFF_WAIT_FILE.write_text(str(nxt))
    except Exception:
        pass


def _clear_backoff() -> None:
    for p in (BACKOFF_FILE, BACKOFF_WAIT_FILE):
        try:
            p.unlink()
        except Exception:
            pass


def fetch_usage() -> Optional[dict]:
    """Return raw API body dict or cached stale copy. None if unreachable + no cache."""
    if _in_backoff():
        cached = _read_cache()
        if cached:
            cached["_stale"] = True
        return cached

    oauth = _keychain_token()
    if not oauth:
        return _read_cache()

    token = oauth.get("accessToken")
    expires = oauth.get("expiresAt")
    if not token:
        return _read_cache()
    if expires and time.time() * 1000 > expires:
        return _read_cache()

    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": ANTHROPIC_BETA,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode())
        _clear_backoff()
        _write_cache(body)
        return body
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _set_backoff()
        cached = _read_cache()
        if cached:
            cached["_stale"] = True
        return cached
    except Exception:
        cached = _read_cache()
        if cached:
            cached["_stale"] = True
        return cached


def summarize(body: dict | None) -> dict:
    """Return {"used": int_pct, "reset": "h:mm local"} for the five_hour window.

    Returns {} when the five_hour window is missing or has no reset time.
    """
    if not body or not isinstance(body, dict):
        return {}
    win = body.get("five_hour")
    if not isinstance(win, dict):
        return {}
    util = win.get("utilization")
    resets_at = win.get("resets_at") or win.get("resetsAt")
    if util is None or not resets_at:
        return {}
    from datetime import datetime
    try:
        local = datetime.fromisoformat(resets_at.replace("Z", "+00:00")).astimezone()
        reset_str = local.strftime("%-I:%M")
    except Exception:
        return {}
    return {"used": int(round(util)), "reset": reset_str}
