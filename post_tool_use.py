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
from pymongo import MongoClient

def get_mongo_client():
    """Get MongoDB client with configurable URI."""
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=500, socketTimeoutMS=1000)

def get_database_name():
    """Generate database name from project directory name."""
    # Use just the project directory name (last part of path)
    project_name = Path.cwd().name
    # Sanitize for MongoDB (remove special chars)
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
        # Read JSON input from stdin
        input_data = json.load(sys.stdin)

        # Update MongoDB document with post-execution data
        if _cfg('mongo_logging'):
            try:
                # Read document ID from temp file
                temp_dir = Path.cwd() / '.claude_temp'
                id_file = temp_dir / 'last_tool_use_id.txt'

                if id_file.exists():
                    with open(id_file, 'r') as f:
                        doc_id = f.read().strip()

                    client = get_mongo_client()
                    db_name = get_database_name()
                    db = client[db_name]
                    collection = db['tool_use']

                    # Update the document with post-execution data
                    update_data = {
                        '$set': {
                            'timestamp_post': datetime.utcnow(),
                            'full_output': input_data,
                            'tool_output': input_data.get('tool_output'),
                            'status': 'complete'
                        }
                    }

                    collection.update_one({'_id': doc_id}, update_data)
                    client.close()

                    # Clean up temp file
                    id_file.unlink()
            except Exception:
                # Fail silently if MongoDB is not available or document not found
                pass

        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Exit cleanly on any other error
        sys.exit(0)

if __name__ == '__main__':
    main()
