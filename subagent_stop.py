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
    pass  # dotenv is optional

DEVICE_NAME = os.getenv("DeNa", "macbook")
SUBAGENT_STOP_TOPIC = f"status/{DEVICE_NAME}/claude/subagent_stop"


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

# ── feature flags ──────────────────────────────────────────────────────────
_HOOKS_CFG = Path.home() / '.claude' / 'hooks_config.json'
def _cfg(key: str, default: bool = True) -> bool:
    try:
        return json.loads(_HOOKS_CFG.read_text()).get(key, default) if _HOOKS_CFG.exists() else default
    except Exception:
        return default
# ───────────────────────────────────────────────────────────────────────────


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


def announce_subagent_completion():
    """Announce subagent completion using the best available TTS service."""
    try:
        tts_script = get_tts_script_path()
        if not tts_script:
            return  # No TTS scripts available

        # Use fixed message for subagent completion
        completion_message = "Subagent Complete"

        # Call the TTS script with the completion message
        subprocess.run([
            "uv", "run", tts_script, completion_message
        ],
        capture_output=True,  # Suppress output
        timeout=10  # 10-second timeout
        )

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        # Fail silently if TTS encounters issues
        pass
    except Exception:
        # Fail silently for any other errors
        pass


def main():
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument('--chat', action='store_true', help='Copy transcript to chat.json')
        parser.add_argument('--notify', action='store_true', help='Enable TTS completion announcement')
        args = parser.parse_args()

        # Read JSON input from stdin
        input_data = json.load(sys.stdin)

        # Extract required fields
        session_id = input_data.get("session_id", "")
        stop_hook_active = input_data.get("stop_hook_active", False)

        # Store data in MongoDB
        if _cfg('mongo_logging'):
            try:
                client = get_mongo_client()
                db_name = get_database_name()
                db = client[db_name]
                collection = db['subagent_stop']

                document = {
                    'session_id': session_id,
                    'stop_hook_active': stop_hook_active,
                    'timestamp': datetime.utcnow(),
                    'full_data': input_data
                }

                collection.insert_one(document)
                client.close()
            except Exception:
                # Fail silently if MongoDB is not available
                pass

        # Handle --chat switch (same as stop.py)
        if args.chat and 'transcript_path' in input_data:
            transcript_path = input_data['transcript_path']
            if os.path.exists(transcript_path):
                # Read .jsonl file and store messages in MongoDB
                chat_messages = []
                try:
                    with open(transcript_path, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    chat_messages.append(json.loads(line))
                                except json.JSONDecodeError:
                                    pass  # Skip invalid lines

                    # Store transcript in MongoDB
                    if chat_messages:
                        try:
                            client = get_mongo_client()
                            db_name = get_database_name()
                            db = client[db_name]
                            collection = db['subagent_transcript']

                            document = {
                                'session_id': session_id,
                                'timestamp': datetime.utcnow(),
                                'messages': chat_messages,
                                'transcript_path': transcript_path
                            }

                            collection.insert_one(document)
                            client.close()
                        except Exception:
                            pass  # Fail silently
                except Exception:
                    pass  # Fail silently

        # MQTT — pair with SubagentStart for full subagent lifecycle
        if _cfg('subagent_mqtt'):
            try:
                post(
                    topic=SUBAGENT_STOP_TOPIC,
                    payload=json.dumps({
                        'session_id': session_id,
                        'parent_session_id': input_data.get('parent_session_id', ''),
                        'stop_hook_active': stop_hook_active,
                        'cwd': str(Path.cwd()),
                        'timestamp': datetime.utcnow().isoformat(),
                        # Close the subagent span MadQTT opened on subagent_start.
                        'agent_id': input_data.get('agent_id', ''),
                        'agent_type': input_data.get('agent_type', ''),
                    }),
                    retain=False,
                )
            except Exception:
                pass

        # Announce subagent completion via TTS (only if --notify flag is set)
        if args.notify and _cfg('subagent_mqtt'):
            announce_subagent_completion()

        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Handle any other errors gracefully
        sys.exit(0)


if __name__ == "__main__":
    # pass
    main()
