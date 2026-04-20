#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
#     "pymongo",
# ]
# ///
#
# Handles: PermissionRequest, PermissionDenied
# Use with --event request|denied flag in settings.json hooks

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
        parser.add_argument('--event', choices=['request', 'denied'], required=True)
        args = parser.parse_args()

        input_data = json.load(sys.stdin)

        session_id = input_data.get('session_id', '')
        tool_name = input_data.get('tool_name', '')
        # PermissionDenied includes 'reason' field
        reason = input_data.get('reason', '')

        # Log to MongoDB
        try:
            client = get_mongo_client()
            db = client[get_database_name()]
            db[f"permission_{args.event}"].insert_one({
                'session_id': session_id,
                'tool_name': tool_name,
                'reason': reason,
                'event': args.event,
                'timestamp': datetime.utcnow(),
                'full_data': input_data
            })
            client.close()
        except Exception:
            pass

        # MQTT — permission denied is especially useful to surface in dashboards
        post(
            topic=f"status/device_name/claude/permission/{args.event}",
            payload=json.dumps({
                'session_id': session_id,
                'tool_name': tool_name,
                'reason': reason,
                'event': args.event,
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
