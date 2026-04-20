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
        input_data = json.load(sys.stdin)

        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {})
        error = input_data.get('error', '')
        session_id = input_data.get('session_id', '')

        # Log to MongoDB — tool failures are useful for debugging patterns
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
        post(
            topic="status/device_name/claude/tool_failure",
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
