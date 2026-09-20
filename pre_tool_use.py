#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
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
import uuid

try:
    from dvttestkit.testKitUtils import post as _mqtt_post
    _MQTT_AVAILABLE = True
except Exception:  # import connects to the broker; any failure means no MQTT
    _MQTT_AVAILABLE = False

DEVICE_NAME = os.getenv("DeNa", "macbook")
ACTIVITY_PRE_TOOL_TOPIC = f"status/{DEVICE_NAME}/activity/pre_tool"

# ── feature flags ──────────────────────────────────────────────────────────
_HOOKS_CFG = Path.home() / '.claude' / 'hooks_config.json'
def _cfg(key: str, default: bool = True) -> bool:
    try:
        return json.loads(_HOOKS_CFG.read_text()).get(key, default) if _HOOKS_CFG.exists() else default
    except Exception:
        return default
# ───────────────────────────────────────────────────────────────────────────

def _fast_exit(code: int = 0):
    """Exit without paying interpreter teardown.

    This hook blocks every tool call, and pymongo's shutdown costs ~575ms —
    roughly 60% of the hook's wall time, for work that is already finished.
    insert_one is server-acked and paho's QoS-0 publish writes to the socket
    inline, so nothing is lost by skipping teardown. os._exit does NOT flush
    stdio, so do that ourselves first.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)

def get_mongo_client():
    """Get MongoDB client with configurable URI."""
    if MongoClient is None:
        return None
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=500, socketTimeoutMS=1000)

def get_database_name():
    """Generate database name from project directory name."""
    # Use just the project directory name (last part of path)
    project_name = Path.cwd().name
    # Sanitize for MongoDB (remove special chars)
    safe_name = project_name.replace('.', '_').replace('-', '_')
    return f"claudehooks_{safe_name}"

def main():
    try:
        # Read JSON input from stdin
        input_data = json.load(sys.stdin)

        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {})

        # MQTT activity heartbeat — SwarmDesk live feed
        if _MQTT_AVAILABLE and _cfg('activity_heartbeat'):
            try:
                # Extract the most meaningful target for this tool call
                target = ''
                if tool_name in ('Read', 'Write', 'Edit', 'MultiEdit'):
                    target = tool_input.get('file_path', '')
                elif tool_name == 'Bash':
                    target = tool_input.get('command', '')[:80]
                elif tool_name == 'Glob':
                    target = tool_input.get('pattern', '')
                elif tool_name == 'Grep':
                    target = tool_input.get('pattern', '')

                _mqtt_post(
                    topic=ACTIVITY_PRE_TOOL_TOPIC,
                    payload=json.dumps({
                        'tool': tool_name,
                        'target': target,
                        'session': input_data.get('session_id', ''),
                        'project': Path.cwd().name,
                        'ts': datetime.utcnow().isoformat(),
                        # Subagent attribution — present only when this tool call
                        # runs inside a Task/subagent. Lets MadQTT group & fold
                        # subagent streams and pair pre/post by tool_use_id.
                        'agent_id': input_data.get('agent_id', ''),
                        'agent_type': input_data.get('agent_type', ''),
                        'tool_use_id': input_data.get('tool_use_id', ''),
                    }),
                    retain=False
                )
            except Exception:
                pass

        # Store data in MongoDB
        if not _cfg('mongo_logging'):
            _fast_exit()
        try:
            client = get_mongo_client()
            db_name = get_database_name()
            db = client[db_name]
            collection = db['tool_use']

            # Create document with unique ID
            doc_id = str(uuid.uuid4())
            document = {
                '_id': doc_id,
                'tool_name': tool_name,
                'tool_input': tool_input,
                'timestamp_pre': datetime.utcnow(),
                'session_id': input_data.get('session_id'),
                'full_input': input_data,
                'status': 'pre'
            }

            collection.insert_one(document)

            # Store document ID in temp file for post_tool_use to find
            temp_dir = Path.cwd() / '.claude_temp'
            temp_dir.mkdir(parents=True, exist_ok=True)
            id_file = temp_dir / 'last_tool_use_id.txt'
            with open(id_file, 'w') as f:
                f.write(doc_id)

            client.close()
        except Exception:
            # Fail silently if MongoDB is not available
            pass

        _fast_exit()

    except json.JSONDecodeError:
        # Gracefully handle JSON decode errors
        _fast_exit()
    except Exception:
        # Handle any other errors gracefully
        _fast_exit()

if __name__ == '__main__':
    main()
