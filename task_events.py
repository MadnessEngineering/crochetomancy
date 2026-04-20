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

        sys.exit(0)

    except (json.JSONDecodeError, Exception):
        sys.exit(0)


if __name__ == '__main__':
    main()
