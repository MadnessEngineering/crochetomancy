#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
#     "pymongo",
# ]
# ///

import argparse
import json
import os
import re
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from pymongo import MongoClient

from dvttestkit.testKitUtils import post
try:
    from dvttestkit.testKitUtils import get as _mqtt_get
    _MQTT_GET_AVAILABLE = True
except ImportError:
    _MQTT_GET_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
MQTT_HOST = os.getenv("MADNESS_MQTT_HOST", "localhost")
DEVICE_NAME = os.getenv("DeNa", "macbook")
SESSION_CONTEXT_TOPIC = f"status/{DEVICE_NAME}/claude/session/context"
GIT_CONTEXT_TOPIC = f"status/{DEVICE_NAME}/claude/git/context"

# ── feature flags ──────────────────────────────────────────────────────────
_HOOKS_CFG = Path.home() / '.claude' / 'hooks_config.json'
_HOOKS_CFG_TOPIC = f"status/{DEVICE_NAME}/claude/hooks_config"
_HOOKS_CFG_DEFAULTS = {
    "activity_heartbeat": True, "context_injection": True,
    "task_sync": True, "mongo_logging": True, "autocommit": True,
    "notification_mqtt": True, "session_mqtt": True, "subagent_mqtt": True,
    "compact_mqtt": True, "permission_mqtt": True,
    "teammate_idle_mqtt": True, "stop_mqtt": True,
    "auto_pull_main": True,
}

def _cfg(key: str, default: bool = True) -> bool:
    try:
        return json.loads(_HOOKS_CFG.read_text()).get(key, default) if _HOOKS_CFG.exists() else default
    except Exception:
        return default

def refresh_hooks_config():
    """Pull retained MQTT config → write ~/.claude/hooks_config.json.
    Session start is the natural refresh point; all hook reads then hit the file."""
    try:
        if _MQTT_GET_AVAILABLE:
            raw = _mqtt_get(topic=_HOOKS_CFG_TOPIC)
            if raw:
                incoming = json.loads(raw) if isinstance(raw, str) else raw
                merged = {**_HOOKS_CFG_DEFAULTS, **incoming}
                _HOOKS_CFG.write_text(json.dumps(merged, indent=2))
                return
    except Exception:
        pass
    # No MQTT or parse error — write defaults if file missing
    if not _HOOKS_CFG.exists():
        _HOOKS_CFG.write_text(json.dumps(_HOOKS_CFG_DEFAULTS, indent=2))
# ───────────────────────────────────────────────────────────────────────────


def get_mongo_client():
    """Get MongoDB client with configurable URI."""
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=500, socketTimeoutMS=1000)

def get_database_name():
    """Generate database name from project directory name."""
    # Use just the project directory name (last part of path)
    project_name = Path.cwd().name
    # Sanitize for MongoDB (remove special chars)
    safe_name = project_name.replace('.', '_').replace('-', '_')
    return f"claudehooks_{safe_name}"

def log_session_start(input_data):
    """Log session start event to MongoDB."""
    try:
        client = get_mongo_client()
        db_name = get_database_name()
        db = client[db_name]
        collection = db['session_start']

        document = {
            'session_id': input_data.get('session_id'),
            'source': input_data.get('source'),
            'timestamp': datetime.utcnow(),
            'full_data': input_data
        }

        collection.insert_one(document)
        client.close()
    except Exception:
        # Fail silently if MongoDB is not available
        pass


def get_git_status():
    """Get current git status information."""
    try:
        # Get current branch
        branch_result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5
        )
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"

        # Get uncommitted changes count
        status_result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if status_result.returncode == 0:
            changes = status_result.stdout.strip().split('\n') if status_result.stdout.strip() else []
            uncommitted_count = len(changes)
        else:
            uncommitted_count = 0

        return current_branch, uncommitted_count
    except Exception:
        return None, None


def get_recent_issues():
    """Get recent GitHub issues if gh CLI is available."""
    try:
        # Check if gh is available
        gh_check = subprocess.run(['which', 'gh'], capture_output=True)
        if gh_check.returncode != 0:
            return None

        # Get recent open issues
        result = subprocess.run(
            ['gh', 'issue', 'list', '--limit', '5', '--state', 'open'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def load_development_context(source):
    """Load relevant development context based on session source."""
    context_parts = []

    # Add timestamp
    context_parts.append(f"Session started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    context_parts.append(f"Session source: {source}")

    # Add git information
    branch, changes = get_git_status()
    if branch:
        context_parts.append(f"Git branch: {branch}")
        if changes > 0:
            context_parts.append(f"Uncommitted changes: {changes} files")

    # Load project-specific context files if they exist
    context_files = [
        ".claude/CONTEXT.md",
        ".claude/TODO.md",
        "TODO.md",
        ".github/ISSUE_TEMPLATE.md"
    ]

    for file_path in context_files:
        if Path(file_path).exists():
            try:
                with open(file_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        context_parts.append(f"\n--- Content from {file_path} ---")
                        context_parts.append(content[:1000])  # Limit to first 1000 chars
            except Exception:
                pass

    # Add recent issues if available
    issues = get_recent_issues()
    if issues:
        context_parts.append("\n--- Recent GitHub Issues ---")
        context_parts.append(issues)

    return "\n".join(context_parts)


def maybe_pull_main():
    """Auto-pull if on main/master. Mirrors the post-checkout git hook for existing repos."""
    try:
        branch_result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, timeout=3
        )
        if branch_result.returncode != 0:
            return
        branch = branch_result.stdout.strip()
        if branch in ('main', 'master'):
            result = subprocess.run(
                ['git', 'pull', '--ff-only'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                # Diverged or no remote — don't block session, just skip
                pass
    except Exception:
        pass


def is_probable_todo_id(session_id):
    """Heuristic: todo launcher passes UUID as session_id."""
    if not session_id or not isinstance(session_id, str):
        return False
    return bool(UUID_PATTERN.match(session_id.strip()))


def as_text(value):
    """Convert scalar/list values to compact text."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join([str(item) for item in value if item is not None]).strip()
    return str(value).strip()


def first_value(document, keys):
    """Return first non-empty value from a list of potential keys."""
    for key in keys:
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, "", []):
            return value
    return None


def slugify(text):
    """Build a safe slug for branch naming hints."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return cleaned[:40] if cleaned else "work"


def find_todo_by_id(todo_id):
    """
    Best-effort lookup for todo metadata from Mongo.
    We intentionally fail soft if no matching database/collection is available.
    """
    client = None
    try:
        client = get_mongo_client()
        preferred = [name for name in [
            os.getenv("OMNISPINDLE_DB"),
            os.getenv("OMNISPINDLE_DB_NAME"),
            "omnispindle",
            "omnispindle_prod",
        ] if name]
        all_dbs = [name for name in client.list_database_names() if "omni" in name.lower()]
        db_candidates = []
        for name in preferred + all_dbs:
            if name not in db_candidates:
                db_candidates.append(name)

        for db_name in db_candidates:
            db = client[db_name]
            collection_names = [c for c in db.list_collection_names() if "todo" in c.lower()]
            for collection_name in collection_names:
                collection = db[collection_name]
                query = {
                    "$or": [
                        {"id": todo_id},
                        {"todo_id": todo_id},
                        {"uuid": todo_id},
                        {"_id": todo_id},
                    ]
                }
                document = collection.find_one(query)
                if document:
                    document["_source_db"] = db_name
                    document["_source_collection"] = collection_name
                    return document
    except Exception:
        return None
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
    return None


def build_todo_context(session_id, source):
    """Build SessionStart additionalContext with todo-aware hints."""
    parts = [
        f"Session source: {source}",
    ]
    branch, _changes = get_git_status()
    if branch:
        parts.append(f"Current branch: {branch}")

    if not is_probable_todo_id(session_id):
        return "\n".join(parts)

    todo = find_todo_by_id(session_id)
    if not todo:
        # Session ID is a UUID but not a known todo — normal Claude session, skip hint
        return "\n".join(parts)

    title = as_text(first_value(todo, ["description", "title", "name", "task"]))
    status = as_text(first_value(todo, ["status", "state"]))
    project = as_text(first_value(todo, ["project"]))
    priority = as_text(first_value(todo, ["priority"]))
    tags = as_text(first_value(todo, ["tags", "labels"]))
    ticket = as_text(first_value(todo, ["ticket", "issue", "issue_id"]))

    parts.append("Todo context (preloaded from Mongo):")
    if title:
        parts.append(f"- Title: {title}")
    if status:
        parts.append(f"- Status: {status}")
    if project:
        parts.append(f"- Project: {project}")
    if priority:
        parts.append(f"- Priority: {priority}")
    if tags:
        parts.append(f"- Tags: {tags}")
    if ticket:
        parts.append(f"- Ticket: {ticket}")

    short_id = session_id.split("-")[0]
    title_slug = slugify(title)
    parts.append(f"Naming hint: branch `todo/{session_id}-{title_slug}`")
    parts.append(f"Commit hint: prefix commit subject with `{session_id}` for traceability.")
    parts.append(
        f"Trace source: {todo.get('_source_db', 'unknown')}.{todo.get('_source_collection', 'unknown')}"
    )
    return "\n".join(parts)


def build_git_context_payload(session_id, source):
    """Build lightweight state for git hooks and other agents."""
    branch, changes = get_git_status()
    payload = {
        "session_id": session_id,
        "source": source,
        "cwd": str(Path.cwd()),
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "branch": branch or "",
        "uncommitted_files": int(changes or 0),
        "active_todo_id": "",
        "todo_short_id": "",
        "todo_title": "",
        "suggested_branch": "",
        "commit_prefix": "",
    }

    if is_probable_todo_id(session_id):
        todo = find_todo_by_id(session_id)
        if todo:
            payload["active_todo_id"] = session_id
            payload["todo_short_id"] = session_id.split("-")[0]
            payload["commit_prefix"] = session_id
            title = as_text(first_value(todo, ["description", "title", "name", "task"]))
            payload["todo_title"] = title
            payload["suggested_branch"] = f"todo/{session_id}-{slugify(title)}"
    return payload


def publish_retained_topic(topic, payload):
    """Publish retained MQTT payload. Fails silently."""
    try:
        subprocess.run(
            ["mosquitto_pub", "-h", MQTT_HOST, "-t", topic, "-m", json.dumps(payload), "-r"],
            capture_output=True,
            timeout=3,
            text=True,
        )
    except Exception:
        pass


def save_git_context_file(payload):
    """Persist local context fallback for git hooks."""
    try:
        git_dir_result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if git_dir_result.returncode != 0:
            return
        git_dir = Path(git_dir_result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = Path.cwd() / git_dir
        state_path = git_dir / "claude-session-context.json"
        state_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def main():
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument('--load-context', action='store_true',
                          help='Load development context at session start')
        parser.add_argument('--announce', action='store_true',
                          help='Announce session start via TTS')
        _args = parser.parse_args()

        # Read JSON input from stdin
        input_data = json.loads(sys.stdin.read())

        # Extract fields
        session_id = input_data.get('session_id', 'unknown')
        source = input_data.get('source', 'unknown')  # "startup", "resume", or "clear"

        # Refresh feature-flag cache from MQTT retained config — all hooks read the file
        refresh_hooks_config()

        # Auto-pull if on main/master (covers existing repos; new repos get the post-checkout git hook)
        if _cfg('auto_pull_main'):
            maybe_pull_main()

        if _cfg('session_mqtt'):
            post(f"{os.getenv('DeNa')}", input_data, True)
        # Log the session start event
        if _cfg('mongo_logging'):
            log_session_start(input_data)

        # Emit compact startup context so the model can act immediately on todo sessions.
        additional_context = build_todo_context(session_id, source)
        git_context_payload = build_git_context_payload(session_id, source)
        publish_retained_topic(SESSION_CONTEXT_TOPIC, git_context_payload)
        publish_retained_topic(GIT_CONTEXT_TOPIC, git_context_payload)
        save_git_context_file(git_context_payload)
        if additional_context:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": additional_context
                }
            }
            print(json.dumps(output))

        # Load development context if requested
        # if args.load_context:
        #     context = load_development_context(source)
        #     if context:
        #         # Using JSON output to add context
        #         output = {
        #             "hookSpecificOutput": {
        #                 "hookEventName": "SessionStart",
        #                 "additionalContext": context
        #             }
        #         }
        #         print(json.dumps(output))
        #         sys.exit(0)

        # Announce session start if requested
        # if args.announce:
        #     try:
        #         # Try to use TTS to announce session start
        #         script_dir = Path(__file__).parent
        #         tts_script = script_dir / "utils" / "tts" / "pyttsx3_tts.py"

        #         if tts_script.exists():
        #             messages = {
        #                 "startup": "Claude Code session started",
        #                 "resume": "Resuming previous session",
        #                 "clear": "Starting fresh session"
        #             }
        #             message = messages.get(source, "Session started")

        #             subprocess.run(
        #                 ["uv", "run", str(tts_script), message],
        #                 capture_output=True,
        #                 timeout=5
        #             )
        #     except Exception:
        #         pass

        # Success
        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Handle any other errors gracefully
        sys.exit(0)


if __name__ == '__main__':
    main()
