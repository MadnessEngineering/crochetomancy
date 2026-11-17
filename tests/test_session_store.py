"""Session data goes to one shared store, never into the cwd's repo.

Run: uv run --python 3.12 --with pymongo --with python-dotenv python tests/test_session_store.py
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import utils.session_store as store  # noqa: E402


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ups = load("ups", "user_prompt_submit.py")
ss = load("ss", "session_start.py")

tmp = Path(tempfile.mkdtemp())
store.SESSIONS_DIR = tmp / "store"
project = tmp / "some-repo"
project.mkdir()
os.chdir(project)

ups.manage_session_data("s-new", "first prompt")
ups.manage_session_data("s-new", "second prompt")
data = json.loads((store.SESSIONS_DIR / "s-new.json").read_text())
assert data["prompts"] == ["first prompt", "second prompt"], data
assert not (project / ".claude").exists(), "wrote into the cwd repo"
print("ok  new session writes to the shared store, nothing in the cwd")

legacy = project / ".claude" / "data" / "sessions"
legacy.mkdir(parents=True)
(legacy / "s-old.json").write_text(json.dumps(
    {"session_id": "s-old", "prompts": ["old"], "agent_name": "Prism"}))
assert ss.read_cached_agent_name("s-old") == "Prism"
print("ok  session_start reads a legacy file before the move")

ups.manage_session_data("s-old", "after the move")
moved = json.loads((store.SESSIONS_DIR / "s-old.json").read_text())
assert moved["agent_name"] == "Prism" and moved["prompts"] == ["old", "after the move"], moved
assert ss.read_cached_agent_name("s-old") == "Prism"
print("ok  a legacy session keeps its agent name and continues in the store")

assert ss.read_cached_agent_name("s-new") is None
assert ss.read_cached_agent_name("missing") is None
print("ok  no agent name: None")

print("all passed")
