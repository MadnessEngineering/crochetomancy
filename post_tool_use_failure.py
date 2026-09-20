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
import sys
from pathlib import Path
from datetime import datetime
try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None  # no mongo available

try:
    from dvttestkit.testKitUtils import post
except Exception:  # import connects to the broker; any failure means no MQTT
    def post(*a, **k): pass  # no broker available

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DEVICE_NAME = os.getenv("DeNa", "macbook")
TOOL_FAILURE_TOPIC = f"status/{DEVICE_NAME}/claude/tool_failure"


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


def main():
    try:
        input_data = json.load(sys.stdin)

        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {})
        error = input_data.get('error', '')
        session_id = input_data.get('session_id', '')

        # Log to MongoDB — tool failures are useful for debugging patterns
        if _cfg('mongo_logging'):
            try:
                client = get_mongo_client()
                db = client[get_database_name()]
                db['tool_failures'].insert_one({
                    'session_id': session_id,
                    'tool_name': tool_name,
                    'error': error,
                    'tool_input': tool_input,
                    'timestamp': datetime.utcnow(),
                    'full_data': input_data
                })
                client.close()
            except Exception:
                pass

        # MQTT — visibility into tool failures across the swarm dashboard
        if _cfg('notification_mqtt'):
            post(
                topic=TOOL_FAILURE_TOPIC,
                payload=json.dumps({
                    'session_id': session_id,
                    'tool_name': tool_name,
                    'error': str(error)[:500],  # truncate long errors
                    'cwd': str(Path.cwd()),
                    'timestamp': datetime.utcnow().isoformat()
                }),
                retain=False
            )

        sys.exit(0)

    except (json.JSONDecodeError, Exception):
        sys.exit(0)


if __name__ == '__main__':
    main()
