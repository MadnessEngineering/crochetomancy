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
import random
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from pymongo import MongoClient

from dvttestkit.testKitUtils import post

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
    """Generate database name from project directory name."""
    # Use just the project directory name (last part of path)
    project_name = Path.cwd().name
    # Sanitize for MongoDB (remove special chars)
    safe_name = project_name.replace('.', '_').replace('-', '_')
    return f"claudehooks_{safe_name}"


def get_completion_messages():
    """Return list of friendly completion messages."""
    return [
        "Work complete!",
        "All done!",
        "Task finished!",
        "Job complete!",
        "Ready for next task!"
    ]


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


def get_llm_completion_message():
    """
    Generate completion message using available LLM services.
    Priority order: OpenAI > Anthropic > Ollama > fallback to random message
    
    Returns:
        str: Generated or fallback completion message
    """
    # Get current script directory and construct utils/llm path
    script_dir = Path(__file__).parent
    llm_dir = script_dir / "utils" / "llm"

    # Try OpenAI first (highest priority)
    if os.getenv('OPENAI_API_KEY'):
        oai_script = llm_dir / "oai.py"
        if oai_script.exists():
            try:
                result = subprocess.run([
                    "uv", "run", str(oai_script), "--completion"
                ],
                capture_output=True,
                text=True,
                timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass

    # Try Anthropic second
    if os.getenv('ANTHROPIC_API_KEY'):
        anth_script = llm_dir / "anth.py"
        if anth_script.exists():
            try:
                result = subprocess.run([
                    "uv", "run", str(anth_script), "--completion"
                ],
                capture_output=True,
                text=True,
                timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass

    # Try Ollama third (local LLM)
    ollama_script = llm_dir / "ollama.py"
    if ollama_script.exists():
        try:
            result = subprocess.run([
                "uv", "run", str(ollama_script), "--completion"
            ],
            capture_output=True,
            text=True,
            timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass

    # Fallback to random predefined message
    messages = get_completion_messages()
    return random.choice(messages)

def announce_completion():
    """Announce completion using the best available TTS service."""
    post(topic="status/device_name/claude/session_stop", payload=str(get_llm_completion_message()), retain=False)
    # try:
    #     tts_script = get_tts_script_path()
    #     if not tts_script:
    #         return  # No TTS scripts available

    #     # Get completion message (LLM-generated or fallback)
    #     completion_message = get_llm_completion_message()

    #     # Call the TTS script with the completion message
    #     subprocess.run([
    #         "uv", "run", tts_script, completion_message
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


def is_git_repository():
    """Check if the current directory is a git repository."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path.cwd()
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def get_staged_diff():
    """Get the staged git diff."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path.cwd()
        )
        return result.stdout if result.stdout.strip() else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

def generate_commit_message_with_ollama(diff: str, model: str = "gpt-oss:20b") -> str | None:
    """Generate a commit message using Ollama."""
    prompt = f"""Generate a raw text commit message for the following diff.
Keep commit message concise and to the point.
Make the first line the title (100 characters max) and the rest the body:
{diff}
"""
    try:
        process = subprocess.Popen(
            ["ollama", "run", model],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path.cwd()
        )
        stdout, _ = process.communicate(input=prompt, timeout=60)

        if process.returncode == 0 and stdout.strip():
            return stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None

def clean_commit_message(message: str) -> str:
    """Clean the generated commit message from LLM thinking artifacts."""
    start_index = message.find('**')
    if start_index != -1:
        return message[start_index:].strip()

    done_thinking_variants = ["done thinking.", "done thinking", "```"]
    for variant in done_thinking_variants:
        if variant in message:
            parts = message.split(variant, 1)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()

    return message


def main():
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser()
        parser.add_argument('--chat', action='store_true', help='Copy transcript to chat.json')
        parser.add_argument('--notify', action='store_true', help='Enable TTS completion announcement')
        parser.add_argument('--autocommit', action='store_true', help='Automatically commit staged changes with a generated message.')
        parser.add_argument('--commit-template', action='store_true', help='Generate a commit message and set it as a template for the next commit.')
        args = parser.parse_args()

        # Read JSON input from stdin
        input_data = json.load(sys.stdin)

        # Handle git operations
        if (args.autocommit or args.commit_template) and is_git_repository():
            diff = get_staged_diff()
            if diff:
                commit_message = generate_commit_message_with_ollama(diff)
                if commit_message:
                    cleaned_message = clean_commit_message(commit_message)

                    if args.autocommit:
                        try:
                            subprocess.run(
                                ["git", "commit", "-m", cleaned_message],
                                check=True,
                                capture_output=True,
                                text=True,
                                cwd=Path.cwd()
                            )
                        except subprocess.CalledProcessError:
                            pass
                    elif args.commit_template:
                        try:
                            git_dir_result = subprocess.run(['git', 'rev-parse', '--git-dir'], capture_output=True, text=True, check=True, cwd=Path.cwd())
                            git_dir = git_dir_result.stdout.strip()
                            template_path = os.path.join(git_dir, "claude_commit_template.txt")
                            with open(template_path, 'w') as f:
                                f.write(cleaned_message)

                            subprocess.run(
                                ["git", "config", "commit.template", template_path],
                                check=True,
                                cwd=Path.cwd()
                            )
                            print("Commit message template set. Run 'git commit' to use it.")
                            print(f"To unset, run: git config --unset commit.template")
                        except (subprocess.CalledProcessError, IOError):
                            pass

        # Extract required fields
        session_id = input_data.get("session_id", "")
        stop_hook_active = input_data.get("stop_hook_active", False)

        # Store data in MongoDB
        try:
            client = get_mongo_client()
            db_name = get_database_name()
            db = client[db_name]
            collection = db['session_stop']

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

        # Handle --chat switch
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
                            collection = db['chat_transcript']

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

        # Announce completion via TTS (only if --notify flag is set)
        if args.notify:
            announce_completion()

        sys.exit(0)

    except json.JSONDecodeError:
        # Handle JSON decode errors gracefully
        sys.exit(0)
    except Exception:
        # Handle any other errors gracefully
        sys.exit(0)


if __name__ == "__main__":
    main()
