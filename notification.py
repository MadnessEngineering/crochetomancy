#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
#     "pymongo",
# ]
# ///

import argparse
import json
import os
import sys
import subprocess
import random
from pathlib import Path
from datetime import datetime
from pymongo import MongoClient
from dvttestkit.testKitUtils import post as _post
from dvttestkit.testKitUtils import get as _get
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional


def get_mongo_client():
    """Get MongoDB client with configurable URI."""
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri)

def get_database_name():
    """Generate database name from project root path."""
    # Use the project root path as database name, sanitized for MongoDB
    root_path = str(Path.cwd()).replace('/', '_').replace('\\', '_').replace('.', '_')
    # Remove leading underscores and limit length
    db_name = root_path.lstrip('_')[:64]
    return f"claude_{db_name}"


def get_tts_script_path():
    """
    Determine which TTS script to use based on available API keys.
    Priority order: ElevenLabs > OpenAI > pyttsx3
    """
    # Get current script directory and construct utils/tts path
    script_dir = Path(__file__).parent
    tts_dir = script_dir / "utils" / "tts"

    # Check for ElevenLabs API key (highest priority)
    if os.getenv('ELEVENLABS_API_KEY'):
        elevenlabs_script = tts_dir / "elevenlabs_tts.py"
        if elevenlabs_script.exists():
            return str(elevenlabs_script)

    # Check for OpenAI API key (second priority)
    if os.getenv('OPENAI_API_KEY'):
        openai_script = tts_dir / "openai_tts.py"
        if openai_script.exists():
            return str(openai_script)

    # Fall back to pyttsx3 (no API key required)
    pyttsx3_script = tts_dir / "pyttsx3_tts.py"
    if pyttsx3_script.exists():
        return str(pyttsx3_script)

    return None


def announce_notification():
    """Announce that the agent needs user input."""
    _post(topic="status/device_name/claude/notification", payload="notification", retain=False)

    # try:
    #     tts_script = get_tts_script_path()
    #     if not tts_script:
    #         return  # No TTS scripts available

    #     # Get engineer name if available
    #     engineer_name = os.getenv('ENGINEER_NAME', '').strip()

    #     # Create notification message with 30% chance to include name
    #     if engineer_name and random.random() < 0.3:
    #         notification_message = f"{engineer_name}, your agent needs your input"
    #     else:
    #         notification_message = "Your agent needs your input"

    #     # Call the TTS script with the notification message
    #     subprocess.run([
    #         "uv", "run", tts_script, notification_message
    #     ],
    #     capture_output=True,  # Suppress output
    #     timeout=10  # 10-second timeout
    #     )

    # except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
    #     # Fail silently if TTS encounters issues
    #     pass
    # except Exception:
    #     # Fail silently for any other errors
    #     pass


def main():
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument('--notify', action='store_true', help='Enable MQTT notifications')
        args = parser.parse_args()

        # Read JSON input from stdin
        input_data = json.loads(sys.stdin.read())

        # Store data in MongoDB
        try:
            client = get_mongo_client()
            db_name = get_database_name()
            db = client[db_name]
            collection = db['notification']

            document = {
                'message': input_data.get('message'),
                'timestamp': datetime.utcnow(),
                'full_data': input_data
            }

            collection.insert_one(document)
            client.close()
        except Exception:
            # Fail silently if MongoDB is not available
            pass

        # Post to MQTT
        _post(topic="status/device_name/claude/notification", payload=str(json.dumps(input_data)), retain=False)

        # Announce notification via TTS only if --notify flag is set
        # Skip TTS for the generic "Claude is waiting for your input" message
        if args.notify and input_data.get('message') != 'Claude is waiting for your input':
            announce_notification()

        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Handle any other errors gracefully
        sys.exit(0)

if __name__ == '__main__':
    main()
