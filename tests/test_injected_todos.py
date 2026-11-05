"""Once-per-session todo injection: each todo is sent once, then only changes.

Run: uv run --python 3.12 --with pymongo --with python-dotenv python tests/test_injected_todos.py
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import utils.injected_todos as memory  # noqa: E402

spec = importlib.util.spec_from_file_location("ups", HOOKS / "user_prompt_submit.py")
ups = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ups)

memory._DIR = Path(tempfile.mkdtemp()) / "injected"

todos = []
ups.fetch_active_todos = lambda project: [dict(t) for t in todos]


def todo(todo_id, status="pending", desc=None):
    return {"id": todo_id, "status": status, "priority": "Medium",
            "description": desc or f"todo {todo_id}"}


def run():
    return ups.fetch_omnispindle_context("proj", "session-1")


def check(label, got, expect_lines):
    lines = got.splitlines() if got else []
    assert lines == expect_lines, f"{label}:\n  got      {lines}\n  expected {expect_lines}"
    print(f"ok  {label}")


todos[:] = [todo("a"), todo("b", "in_progress"), todo("c")]
check("first prompt shows every active todo", run(), [
    "[proj todos — 3 active]",
    "• [Medium] todo a",
    "• [Medium] (in_progress) todo b",
    "• [Medium] todo c",
])

check("same todos again: nothing injected", run(), [])

todos[:] = [todo("a", "in_progress"), todo("b", "in_progress"), todo("d")]
check("status change, new todo, removed todo", run(), [
    "[proj todos — changes since last shown]",
    "• [Medium] (pending → in_progress) todo a",
    "• [Medium] todo d",
    "No longer in the active list: todo c",
])

check("no change after reporting: nothing injected", run(), [])

todos[:] = [todo("a", "in_progress"), todo("b", "in_progress"), todo("c"), todo("d")]
check("removed todo returns: shown as new", run(), [
    "[proj todos — changes since last shown]",
    "• [Medium] todo c",
])

memory.clear_shown("session-1")
check("after compaction or /clear: full block again", run(), [
    "[proj todos — 4 active]",
    "• [Medium] (in_progress) todo a",
    "• [Medium] (in_progress) todo b",
    "• [Medium] todo c",
    "• [Medium] todo d",
])

todos[:] = []
memory.clear_shown("session-1")
check("no active todos: nothing injected", run(), [])

ups.fetch_active_todos = lambda project: None
check("store unavailable: nothing injected", run(), [])

print("all passed")
