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
from pathlib import Path
from datetime import datetime
try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None  # no mongo available

from utils.session_color import apply_session_color

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

try:
    from dvttestkit.testKitUtils import post
except Exception:  # import connects to the broker; any failure means no MQTT
    def post(*a, **k): pass  # no broker available

DEVICE_NAME = os.getenv("DeNa", "macbook")
USER_PROMPT_TOPIC = f"status/{DEVICE_NAME}/claude/user_prompt"
# Prompt TEXT goes to the (anonymous) broker only when a machine opts in —
# meant for headless fleet boxes (the sentinel VM) whose session viewers want
# content, never as a default for interactive dev machines.
MQTT_CONTENT = os.getenv("CLAUDE_HOOKS_MQTT_CONTENT", "") == "1"

# Omnispindle project names derived from cwd path parts
_PROJECT_NAMES = {
    'omnispindle', 'inventorium', 'anathesmelt', 'swarmdesk',
    'swarmonomicon', 'todomill_projectorium', 'hammerspoon',
    'madness_interactive', 'chrome-devtools-mcp', 'lab',
}

# ── feature flags ──────────────────────────────────────────────────────────
_HOOKS_CFG = Path.home() / '.claude' / 'hooks_config.json'
def _cfg(key: str, default: bool = True) -> bool:
    try:
        return json.loads(_HOOKS_CFG.read_text()).get(key, default) if _HOOKS_CFG.exists() else default
    except Exception:
        return default
# ───────────────────────────────────────────────────────────────────────────

def detect_project():
    """Derive Omnispindle project name from current working directory."""
    parts = [p.lower().replace('-', '_') for p in Path.cwd().parts]
    for part in reversed(parts):
        if part in _PROJECT_NAMES:
            return part
    return Path.cwd().name.lower().replace('-', '_')


def fetch_omnispindle_context(project: str) -> str | None:
    """Query swarmonomicon todos for active items in this project.
    Returns a compact context string, or None if unavailable."""
    try:
        mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
        db_name = os.getenv('MONGODB_DB', 'swarmonomicon')
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        db = client[db_name]

        active = list(db['todos'].find(
            {'project': project, 'status': {'$in': ['pending', 'in_progress', 'blocked', 'initial']}},
            {'description': 1, 'priority': 1, 'status': 1, 'id': 1, '_id': 0},
            limit=8
        ))
        client.close()

        if not active:
            return None

        lines = [f"[{project} todos — {len(active)} active]"]
        for t in active:
            pri = t.get('priority', 'Medium')
            status = t.get('status', 'pending')
            desc = t.get('description', '')[:100]
            status_tag = f" ({status})" if status not in ('pending', 'initial') else ''
            lines.append(f"• [{pri}]{status_tag} {desc}")
        return '\n'.join(lines)
    except Exception:
        return None


def get_mongo_client():
    """Get MongoDB client with configurable URI."""
    if MongoClient is None:
        return None
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri)

def get_database_name():
    """Generate database name from project directory name."""
    # Use just the project directory name (last part of path)
    project_name = Path.cwd().name
    # Sanitize for MongoDB (remove special chars)
    safe_name = project_name.replace('.', '_').replace('-', '_')
    return f"claudehooks_{safe_name}"

def log_user_prompt(session_id, input_data):
    """Log user prompt to MongoDB."""
    try:
        client = get_mongo_client()
        db_name = get_database_name()
        db = client[db_name]
        collection = db['user_prompt']

        document = {
            'session_id': session_id,
            'prompt': input_data.get('prompt'),
            'timestamp': datetime.utcnow(),
            'full_data': input_data
        }

        collection.insert_one(document)
        client.close()
    except Exception:
        # Fail silently if MongoDB is not available
        pass


# Legacy function removed - now handled by manage_session_data


def manage_session_data(session_id, prompt, name_agent=False):
    """Manage session data in the new JSON structure."""
    import subprocess

    # Ensure sessions directory exists
    sessions_dir = Path(".claude/data/sessions")
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Load or create session file
    session_file = sessions_dir / f"{session_id}.json"

    if session_file.exists():
        try:
            with open(session_file, 'r') as f:
                session_data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            session_data = {"session_id": session_id, "prompts": []}
    else:
        session_data = {"session_id": session_id, "prompts": []}

    # Add the new prompt
    session_data["prompts"].append(prompt)

    # Generate agent name if requested and not already present
    if name_agent and "agent_name" not in session_data:
        # Resolve helpers next to this hook — cwd is the user's project, not the clone.
        llm_dir = Path(__file__).parent / "utils" / "llm"
        # Try Ollama first (preferred)
        try:
            result = subprocess.run(
                ["uv", "run", str(llm_dir / "ollama.py"), "--agent-name"],
                capture_output=True,
                text=True,
                timeout=5  # Shorter timeout for local Ollama
            )

            if result.returncode == 0 and result.stdout.strip():
                agent_name = result.stdout.strip()
                # Check if it's a valid name (not an error message)
                if len(agent_name.split()) == 1 and agent_name.isalnum():
                    session_data["agent_name"] = agent_name
                else:
                    raise Exception("Invalid name from Ollama")
        except Exception:
            # Fall back to Anthropic if Ollama fails
            try:
                result = subprocess.run(
                    ["uv", "run", str(llm_dir / "anth.py"), "--agent-name"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if result.returncode == 0 and result.stdout.strip():
                    agent_name = result.stdout.strip()
                    # Validate the name
                    if len(agent_name.split()) == 1 and agent_name.isalnum():
                        session_data["agent_name"] = agent_name
            except Exception:
                # If both fail, don't block the prompt
                pass

    # Save the updated session data
    try:
        with open(session_file, 'w') as f:
            json.dump(session_data, f, indent=2)
    except Exception:
        # Silently fail if we can't write the file
        pass

    return session_data


def validate_prompt(prompt):
    """
    Validate the user prompt for security or policy violations.
    Returns tuple (is_valid, reason).
    """
    # Example validation rules (customize as needed)
    blocked_patterns = [
        # Add any patterns you want to block
        # Example: ('rm -rf /', 'Dangerous command detected'),
    ]

    prompt_lower = prompt.lower()

    for pattern, reason in blocked_patterns:
        if pattern.lower() in prompt_lower:
            return False, reason

    return True, None


def main():
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument('--validate', action='store_true',
                          help='Enable prompt validation')
        parser.add_argument('--log-only', action='store_true',
                          help='Only log prompts, no validation or blocking')
        parser.add_argument('--store-last-prompt', action='store_true',
                          help='Store the last prompt for status line display')
        parser.add_argument('--name-agent', action='store_true',
                          help='Generate an agent name for the session')
        parser.add_argument('--inject-context', action='store_true',
                          help='Inject active Omnispindle todos as additionalContext')
        args = parser.parse_args()

        # Read JSON input from stdin
        input_data = json.loads(sys.stdin.read())

        # Extract session_id and prompt
        session_id = input_data.get('session_id', 'unknown')
        prompt = input_data.get('prompt', '')

        # Log the user prompt
        if _cfg('mongo_logging'):
            log_user_prompt(session_id, input_data)

        # Opt-in MQTT publish of the prompt head, so fleet session viewers
        # (Sentinel Agents grid, MadQTT) can show WHAT a session was asked.
        if MQTT_CONTENT and _cfg('session_mqtt'):
            try:
                post(topic=USER_PROMPT_TOPIC, payload=json.dumps({
                    'session_id': session_id,
                    'cwd': str(Path.cwd()),
                    'timestamp': datetime.utcnow().isoformat(),
                    'prompt': str(prompt)[:400],
                }), retain=False)
            except Exception:
                pass  # telemetry must never block the prompt

        # Retry session color each prompt: the transcript / job state.json may not
        # exist yet at SessionStart. Idempotent, and a user /color always wins.
        if _cfg('session_color'):
            apply_session_color(input_data)

        # Manage session data with JSON structure
        session_data = None
        if args.store_last_prompt or args.name_agent:
            session_data = manage_session_data(session_id, prompt, name_agent=args.name_agent)

        # Validate prompt if requested and not in log-only mode
        if args.validate and not args.log_only:
            is_valid, reason = validate_prompt(prompt)
            if not is_valid:
                # Exit code 2 blocks the prompt with error message
                print(f"Prompt blocked: {reason}", file=sys.stderr)
                sys.exit(2)

        # Build a single hookSpecificOutput combining the upgraded session title
        # (full UUID + descriptive slug) and any injected context.
        hook_output = {"hookEventName": "UserPromptSubmit"}

        if args.name_agent and session_data and session_data.get("agent_name"):
            hook_output["sessionTitle"] = f"{session_id} · {session_data['agent_name']}"

        if args.inject_context and _cfg('context_injection'):
            project = detect_project()
            context = fetch_omnispindle_context(project)
            if context:
                hook_output["additionalContext"] = context

        # Only emit when we have something beyond the bare event name
        if len(hook_output) > 1:
            print(json.dumps({"hookSpecificOutput": hook_output}))

        # Success - prompt will be processed
        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Handle any other errors gracefully
        sys.exit(0)


if __name__ == '__main__':
    main()
