"""Git-aware work context for SessionStart (Git Archaeology F04).

Takes a local git snapshot (`madhub snapshot --json`, or a minimal git
fallback when the installed madhub predates that command), sends the touched
files to Omnispindle's get_context_bundle, and formats the todos and lessons
that match the work into a short block for additionalContext. Every item
carries the reason it was picked.

Gated by hooks_config key `git_context_injection` (default off). Everything
here fails closed: any error means no block, never a broken session start.
The madnessDesktop preload sampler imports build_work_context() so it shows
exactly what this hook would inject.
"""

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

TIMEOUT_S = 4
MAX_ITEMS = 3


def _git(cwd, *args):
    r = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=TIMEOUT_S,
    )
    return r.stdout if r.returncode == 0 else ""


def _fallback_snapshot(cwd):
    top = _git(cwd, "rev-parse", "--show-toplevel").strip()
    if not top:
        return None
    snap = {"path": top, "project": Path(top).name.lower().replace("-", "_"),
            "branch": "", "upstream": None, "ahead": 0, "behind": 0,
            "changed": [], "commits": [], "stash": 0, "inProgress": None,
            "source": "git-fallback"}
    for line in _git(top, "status", "--porcelain=v2", "--branch").splitlines():
        if line.startswith("# branch.head "):
            snap["branch"] = line[14:]
        elif line.startswith("# branch.upstream "):
            snap["upstream"] = line[18:]
        elif line.startswith("# branch.ab "):
            a, b = line[12:].split()
            snap["ahead"], snap["behind"] = int(a), -int(b)
        elif line[:2] in ("1 ", "2 ", "u "):
            snap["changed"].append(line.split(" ")[-1].split("\t")[0])
        elif line.startswith("? "):
            snap["changed"].append(line[2:])
    cur = None
    for line in _git(top, "log", "-5", "--name-only", "--format=@@%h%x09%cr%x09%s").splitlines():
        if line.startswith("@@"):
            sha, when, subject = (line[2:].split("\t") + ["", ""])[:3]
            cur = {"sha": sha, "when": when, "subject": subject, "files": []}
            snap["commits"].append(cur)
        elif line.strip() and cur:
            cur["files"].append(line.strip())
    snap["stash"] = len([l for l in _git(top, "stash", "list").splitlines() if l])
    return snap


def _madhub_with_snapshot():
    """Path to a madhub that knows `snapshot`, else None.

    An installed madhub that predates the command treats `snapshot` as a path
    and opens the app on an "Add Local Repository" dialog, so check the usage
    text first (`--help` only prints and exits).
    """
    madhub = shutil.which("madhub")
    if not madhub:
        return None
    try:
        r = subprocess.run([madhub, "--help"], capture_output=True, text=True, timeout=TIMEOUT_S)
        return madhub if "madhub snapshot" in (r.stdout + r.stderr) else None
    except Exception:
        return None


def take_snapshot(cwd=None):
    """Return one repo snapshot dict, or None outside a repo."""
    cwd = Path(cwd or Path.cwd())
    madhub = _madhub_with_snapshot()
    if madhub:
        try:
            r = subprocess.run([madhub, "snapshot", "--json", str(cwd)],
                               capture_output=True, text=True, timeout=TIMEOUT_S)
            repos = json.loads(r.stdout).get("repos") if r.returncode == 0 else None
            if repos and not repos[0].get("error"):
                return {**repos[0], "source": "madhub"}
        except Exception:
            pass
    try:
        return _fallback_snapshot(cwd)
    except Exception:
        return None


def _mcp_server():
    cfg = json.loads((Path.home() / ".claude.json").read_text())
    common = Path.home() / "lab/madness_interactive/projects/common/madnessDesktop"
    s = (cfg.get("projects", {}).get(str(common), {}).get("mcpServers", {}).get("omnispindle")
         or cfg.get("mcpServers", {}).get("omnispindle") or {})
    if not s.get("url"):
        raise RuntimeError("no HTTP omnispindle server in ~/.claude.json")
    return s["url"], s.get("headers", {})


def _mcp_tool(name, args):
    url, headers = _mcp_server()
    session = {}

    def post(body):
        h = {**headers, "content-type": "application/json",
             "accept": "application/json, text/event-stream"}
        if session.get("id"):
            h["mcp-session-id"] = session["id"]
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            sid = res.headers.get("mcp-session-id")
            if sid:
                session["id"] = sid
            text = res.read().decode()
            if "id" not in body:
                return None
            if "text/event-stream" in (res.headers.get("content-type") or ""):
                for line in text.splitlines():
                    if line.startswith("data:"):
                        msg = json.loads(line[5:])
                        if msg.get("id") == body["id"]:
                            return msg
                raise RuntimeError("no matching event in MCP stream")
            return json.loads(text)

    post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "session-start-work-context", "version": "0.1"}}})
    post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    msg = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": args}})
    if msg.get("error"):
        raise RuntimeError(msg["error"].get("message", "MCP error"))
    raw = "\n".join(c.get("text", "") for c in msg.get("result", {}).get("content", []))
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def _short(s, n=90):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def format_block(snap, wc):
    ab = f" ↑{snap.get('ahead', 0)} ↓{snap.get('behind', 0)}" if snap.get("upstream") else " (no upstream)"
    changed = snap.get("changed") or []
    lines = [f"[git] {Path(snap['path']).name} @ {snap.get('branch') or '?'}{ab} · "
             f"{len(changed)} changed · stash {snap.get('stash', 0)}"
             + (f" · {str(snap['inProgress']).upper()} IN PROGRESS" if snap.get("inProgress") else "")]
    commits = snap.get("commits") or []
    if commits:
        c = commits[0]
        lines.append(f"last: {c.get('sha')} {_short(c.get('subject'))} ({c.get('when')})")
    if changed:
        lines.append("changed: " + ", ".join(changed[:6]) + (f" +{len(changed) - 6}" if len(changed) > 6 else ""))
    todos = (wc or {}).get("todos") or []
    if todos:
        lines.append("[todos for this work]")
        for t in todos[:MAX_ITEMS]:
            why = "; ".join(t.get("why") or [])
            lines.append(f"• {str(t.get('id', ''))[:8]} [{t.get('priority') or '-'}] {_short(t.get('description'))}"
                         + (f" (why: {why})" if why else ""))
    lessons = (wc or {}).get("lessons") or []
    # Keyword (regex) lesson hits were noise in the sampler; only inject real similarity matches.
    if lessons and (wc or {}).get("method", (wc or {}).get("lessons_method")) == "semantic":
        lines.append("[lessons]")
        for l in lessons[:MAX_ITEMS]:
            why = "; ".join(l.get("why") or []) if isinstance(l.get("why"), list) else (l.get("why") or "")
            lines.append(f"• {str(l.get('id', ''))[:8]} {_short(l.get('topic'))}" + (f" (why: {why})" if why else ""))
    if todos or lessons:
        lines.append("(get_todo / get_lesson <id> for detail)")
    return "\n".join(lines)


def build_work_context(cwd=None, limit=MAX_ITEMS):
    """Return (text, debug). text is None when nothing should be injected."""
    debug = {}
    snap = take_snapshot(cwd)
    if not snap:
        return None, {"reason": "not a git repo"}
    debug["snapshot_source"] = snap.get("source")
    touched = list(dict.fromkeys([*(snap.get("changed") or []),
                                  *[f for c in snap.get("commits") or [] for f in c.get("files", [])]]))
    query = " ; ".join([c.get("subject", "") for c in snap.get("commits") or []] + touched[:8])
    try:
        data = _mcp_tool("get_context_bundle", {
            "project": snap["project"], "files": touched, "branch": snap.get("branch") or None,
            "query": query, "limit": limit,
        })
        bundle = data.get("bundle", data) if isinstance(data, dict) else {}
        wc = bundle.get("work_context") or (data.get("data", {}) if isinstance(data, dict) else {}).get("bundle", {}).get("work_context")
        debug["work_context"] = wc
        if wc is None:
            debug["reason"] = "server returned no work_context (old server?)"
    except Exception as e:
        wc = None
        debug["reason"] = f"omnispindle call failed: {e}"
    return format_block(snap, wc), debug
