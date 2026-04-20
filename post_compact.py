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

        session_id = input_data.get('session_id', '')
        trigger = input_data.get('trigger', 'unknown')  # "manual" or "auto"
        summary = input_data.get('summary', '')

        # Log to MongoDB
        try:
            client = get_mongo_client()
            db = client[get_database_name()]
            db['post_compact'].insert_one({
                'session_id': session_id,
                'trigger': trigger,
                'summary': summary,
                'timestamp': datetime.utcnow(),
                'full_data': input_data
            })
            client.close()
        except Exception:
            pass

        # MQTT — pairs with PreCompact, signals compaction is done and context refreshed
        post(
            topic="status/device_name/claude/post_compact",
            payload=json.dumps({
                'session_id': session_id,
                'trigger': trigger,
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
