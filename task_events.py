#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
#     "pymongo",
# ]
# ///
#
# Handles: TaskCreated, TaskCompleted
# Use with --event created|completed flag in settings.json hooks
#
# Cross-agent chat potential: task events are broadcast on mqtt so
# sibling agents and the SwarmDesk dashboard can see work in-flight.
# Subscribe to status/device_name/claude/task/# to watch all agents.

import argparse
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


def get_mongo_client():
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri)

def get_database_name():
    project_name = Path.cwd().name
    safe_name = project_name.replace('.', '_').replace('-', '_')
    return f"claudehooks_{safe_name}"

_PROJECT_NAMES = {
    'omnispindle', 'inventorium', 'anathesmelt', 'swarmdesk',
    'swarmonomicon', 'todomill_projectorium', 'hammerspoon',
    'madness_interactive', 'chrome-devtools-mcp', 'lab',
}

def detect_omnispindle_project():
    parts = [p.lower().replace('-', '_') for p in Path.cwd().parts]
    for part in reversed(parts):
        if part in _PROJECT_NAMES:
            return part
    return Path.cwd().name.lower().replace('-', '_')


def mirror_to_omnispindle(task_id: str, title: str, description: str, session_id: str, event: str):
    """Mirror a Claude Code task into the Omnispindle swarmonomicon todos collection."""
    try:
        mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
        db_name = os.getenv('MONGODB_DB', 'swarmonomicon')
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        db = client[db_name]

        if event == 'created':
            import uuid as _uuid
            doc = {
                'id': str(_uuid.uuid4()),
                'description': title or description,
                'project': detect_omnispindle_project(),
                'status': 'in_progress',
                'priority': 'Medium',
                'created_at': int(datetime.utcnow().timestamp()),
                'target_agent': 'AI',
                'notes': description[:300] if description else '',
                'metadata': {
                    'claude_task_id': task_id,
                    'claude_session_id': session_id,
                    'source': 'claude_code_task_hook',
                    'files': [str(Path.cwd())]
                }
            }
            db['todos'].insert_one(doc)
        elif event == 'completed':
            # Mark matching todo as review
            db['todos'].update_one(
                {'metadata.claude_task_id': task_id},
                {'$set': {'status': 'review', 'completed_at': int(datetime.utcnow().timestamp())}}
            )
        client.close()
    except Exception:
        pass


def main():
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--event', choices=['created', 'completed'], required=True,
                            help='Which task event fired')
        args = parser.parse_args()

        input_data = json.load(sys.stdin)

        session_id = input_data.get('session_id', '')
        task_id = input_data.get('task_id', '')
        title = input_data.get('title', '')
        description = input_data.get('description', '')

        # Log to MongoDB
        try:
            client = get_mongo_client()
            db = client[get_database_name()]
            collection_name = f"task_{args.event}"
            db[collection_name].insert_one({
                'session_id': session_id,
                'task_id': task_id,
                'title': title,
                'description': description,
                'event': args.event,
                'timestamp': datetime.utcnow(),
                'full_data': input_data
            })
            client.close()
        except Exception:
            pass

        # MQTT — broadcast task lifecycle on a hierarchical topic
        # Subscribe to status/device_name/claude/task/# for all task events
        # or status/device_name/claude/task/created for just new tasks
        mqtt_payload = json.dumps({
            'session_id': session_id,
            'task_id': task_id,
            'title': title,
            'description': description[:300] if description else '',
            'event': args.event,
            'cwd': str(Path.cwd()),
            'timestamp': datetime.utcnow().isoformat()
        })

        post(
            topic=f"status/device_name/claude/task/{args.event}",
            payload=mqtt_payload,
            retain=False
        )

        # Mirror into Omnispindle swarmonomicon todos
        mirror_to_omnispindle(task_id, title, description, session_id, args.event)

        sys.exit(0)

    except (json.JSONDecodeError, Exception):
        sys.exit(0)


if __name__ == '__main__':
    main()
