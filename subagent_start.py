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
except ImportError:
    def post(*a, **k): pass  # no broker available

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DEVICE_NAME = os.getenv("DeNa", "macbook")
SUBAGENT_START_TOPIC = f"status/{DEVICE_NAME}/claude/subagent_start"


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

        session_id = input_data.get('session_id', '')
        parent_session_id = input_data.get('parent_session_id', '')

        # Log to MongoDB
        if _cfg('mongo_logging'):
            try:
                client = get_mongo_client()
                db = client[get_database_name()]
                db['subagent_start'].insert_one({
                    'session_id': session_id,
                    'parent_session_id': parent_session_id,
                    'timestamp': datetime.utcnow(),
                    'full_data': input_data
                })
                client.close()
            except Exception:
                pass

        # MQTT publish — pair with SubagentStop for full subagent lifecycle
        if _cfg('subagent_mqtt'):
            post(
                topic=SUBAGENT_START_TOPIC,
                payload=json.dumps({
                    'session_id': session_id,
                    'parent_session_id': parent_session_id,
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
