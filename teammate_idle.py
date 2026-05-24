#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
#     "pymongo",
# ]
# ///
#
# Handles: TeammateIdle
#
# Cross-agent coordination via MQTT:
# When an agent goes idle (waiting for user input), it publishes its state
# to the swarm bus. Other agents or n8n automations can subscribe to
# status/{DeNa}/claude/swarm/idle and:
#   - Show a unified "who's waiting" view in SwarmDesk
#   - Route messages between agents via mqtt
#   - Trigger automated handoffs or context sharing
#
# To send a message TO an idle agent, publish to:
#   status/{DeNa}/claude/swarm/chat_in/{session_id}
# (wire that up via n8n or another agent's hook)

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from pymongo import MongoClient

from dvttestkit.testKitUtils import post

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DEVICE_NAME = os.getenv("DeNa", "macbook")
SWARM_IDLE_TOPIC = f"status/{DEVICE_NAME}/claude/swarm/idle"
SWARM_CHAT_IN_TOPIC_PREFIX = f"status/{DEVICE_NAME}/claude/swarm/chat_in"


def get_mongo_client():
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
        # TeammateIdle may include info about which teammate is idle
        teammate_session_id = input_data.get('teammate_session_id', '')
        message = input_data.get('message', '')

        cwd = str(Path.cwd())
        project_name = Path.cwd().name

        # Log to MongoDB
        if _cfg('mongo_logging'):
            try:
                client = get_mongo_client()
                db = client[get_database_name()]
                db['teammate_idle'].insert_one({
                    'session_id': session_id,
                    'teammate_session_id': teammate_session_id,
                    'message': message,
                    'project': project_name,
                    'timestamp': datetime.utcnow(),
                    'full_data': input_data
                })
                client.close()
            except Exception:
                pass

        # MQTT swarm bus — broadcast idle state for cross-agent coordination
        if _cfg('teammate_idle_mqtt'):
            swarm_payload = json.dumps({
                'session_id': session_id,
                'teammate_session_id': teammate_session_id,
                'message': message,
                'project': project_name,
                'cwd': cwd,
                'timestamp': datetime.utcnow().isoformat(),
                # Hint: publish to SWARM_CHAT_IN_TOPIC_PREFIX/{session_id}
                # to inject a message back into this agent
                'reply_topic': f"{SWARM_CHAT_IN_TOPIC_PREFIX}/{session_id}"
            })

            post(
                topic=SWARM_IDLE_TOPIC,
                payload=swarm_payload,
                retain=False
            )

        sys.exit(0)

    except (json.JSONDecodeError, Exception):
        sys.exit(0)


if __name__ == '__main__':
    main()
