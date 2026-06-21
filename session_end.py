#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
#     "pymongo",
# ]
# ///

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime
try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None  # no mongo available

try:
    from dvttestkit.testKitUtils import post
except ImportError:
    def post(*a, **k): pass  # no broker available

try:
    from utils.usage_stats import fetch_usage, summarize
except Exception:
    def fetch_usage(): return None
    def summarize(_): return {}

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MQTT_HOST = os.getenv("MADNESS_MQTT_HOST", "localhost")
DEVICE_NAME = os.getenv("DeNa", "macbook")
SESSION_CONTEXT_TOPIC = f"status/{DEVICE_NAME}/claude/session/context"
GIT_CONTEXT_TOPIC = f"status/{DEVICE_NAME}/claude/git/context"
SESSION_END_TOPIC = f"status/{DEVICE_NAME}/claude/session_end"


def get_mongo_client():
    if MongoClient is None:
        return None
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=500, socketTimeoutMS=1000)

def get_database_name():
    project_name = Path.cwd().name
    safe_name = project_name.replace('.', '_').replace('-', '_')
    return f"claudehooks_{safe_name}"

# ── feature flags ──────────────────────────────────────────────────────────
_HOOKS_CFG = Path.home() / '.claude' / 'hooks_config.json'
def _cfg(key: str, default: bool = True) -> bool:
    try:
        return json.loads(_HOOKS_CFG.read_text()).get(key, default) if _HOOKS_CFG.exists() else default
    except Exception:
        return default
# ───────────────────────────────────────────────────────────────────────────

def git_push():
    pass


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


def clear_git_context_file():
    """Best-effort cleanup of local git context state."""
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
        if state_path.exists():
            state_path.unlink()
    except Exception:
        pass


def main():
    try:
        input_data = json.load(sys.stdin)

        session_id = input_data.get('session_id', '')
        ended_payload = {
            "session_id": session_id,
            "source": "session_end",
            "cwd": str(Path.cwd()),
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "state": "ended",
            "active_todo_id": "",
            "todo_short_id": "",
            "todo_title": "",
            "suggested_branch": "",
            "commit_prefix": "",
        }

        # Log to MongoDB
        if _cfg('mongo_logging'):
            try:
                client = get_mongo_client()
                db = client[get_database_name()]
                db['session_end'].insert_one({
                    'session_id': session_id,
                    'timestamp': datetime.utcnow(),
                    'full_data': input_data
                })
                client.close()
            except Exception:
                pass

        # MQTT — SessionEnd complements SessionStart for full lifecycle tracking
        if _cfg('session_mqtt'):
            payload = {
                'session_id': session_id,
                'cwd': str(Path.cwd()),
                'timestamp': datetime.utcnow().isoformat(),
            }
            usage = summarize(fetch_usage()) if _cfg('usage_stats') else {}
            if usage:
                payload['usage'] = usage
            post(
                topic=SESSION_END_TOPIC,
                payload=json.dumps(payload),
                retain=False
            )
        publish_retained_topic(SESSION_CONTEXT_TOPIC, ended_payload)
        publish_retained_topic(GIT_CONTEXT_TOPIC, ended_payload)
        clear_git_context_file()

        sys.exit(0)

    except (json.JSONDecodeError, Exception):
        sys.exit(0)



if __name__ == '__main__':
    main()
    git_push()
