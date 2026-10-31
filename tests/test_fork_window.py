"""Fork hook: Shellomancy tab first, Warp window fallback.

Run from the hooks dir: python3 -m unittest discover -s tests -p "test_fork_window.py" -v
Uses the real shellomancy_client against a fake app (Unix-socket broker +
loopback HTTP), so the request the hook sends is checked end to end.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOKS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS_DIR))

from utils import fork_window  # noqa: E402

CLIENT_DIR = fork_window.SHELLOMANCY_CLIENT_DIR
FAKE_APP = CLIENT_DIR / "test_shellomancy_client.py"


def _load_fake_app():
    sys.path.insert(0, str(CLIENT_DIR))
    spec = importlib.util.spec_from_file_location("shellomancy_client_tests", FAKE_APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FakeApp


@unittest.skipUnless(FAKE_APP.is_file(), "shellomancy checkout not found")
class ForkInShellomancyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="smf")
        self.dir = Path(self.tmp.name)
        self.env = mock.patch.dict(
            os.environ, {"WARP_LOCAL_CONTROL_DISCOVERY_DIR": str(self.dir)}
        )
        self.env.start()
        self.app = _load_fake_app()(self.dir)

    def tearDown(self):
        self.app.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_opens_tab_running_claude_attach(self):
        self.app.write_record()
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/Users/x/.emclau"}):
            opened = fork_window.open_fork_in_shellomancy("1f8b2155", "/tmp", "anathesmelt", "purple")

        self.assertTrue(opened)
        params = self.app.http_requests[0][2]["action"]["params"]
        self.assertEqual(params["title"], "⑂ anathesmelt 1f8b2155")
        self.assertEqual(params["color"], "magenta")
        self.assertEqual(params["cwd"], "/tmp")
        self.assertTrue(params["command"].startswith("CLAUDE_CONFIG_DIR=/Users/x/.emclau "))
        self.assertTrue(params["command"].endswith(" attach 1f8b2155"))
        self.assertNotIn("\n", params["command"])

    def test_returns_false_without_a_running_instance(self):
        self.assertFalse(
            fork_window.open_fork_in_shellomancy("1f8b2155", "/tmp", "anathesmelt", "blue")
        )
        self.assertEqual(self.app.http_requests, [])

    def test_returns_false_when_the_app_refuses(self):
        self.app.reply = lambda envelope: {
            "status": "error",
            "error": {"code": "insufficient_permissions", "message": "no commands"},
        }
        self.app.write_record()
        self.assertFalse(
            fork_window.open_fork_in_shellomancy("1f8b2155", "/tmp", "anathesmelt", "blue")
        )


class OpenForkWindowTest(unittest.TestCase):
    def setUp(self):
        # detect_project shells out to git; keep subprocess mocks to the hook's own calls.
        patcher = mock.patch.object(fork_window, "detect_project", return_value="anathesmelt")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _input(self, session_id="1f8b2155-aaaa-bbbb-cccc-000000000000"):
        return {"source": "fork", "session_id": session_id, "cwd": "/tmp"}

    def test_prefers_shellomancy_and_skips_warp(self):
        with mock.patch.object(fork_window.sys, "platform", "darwin"), \
             mock.patch.object(Path, "is_dir", return_value=True), \
             mock.patch.object(fork_window, "open_fork_in_shellomancy", return_value=True) as tab, \
             mock.patch.object(fork_window, "_write_launch_config") as warp:
            self.assertEqual(fork_window.open_fork_window(self._input()), "shellomancy")
        tab.assert_called_once()
        warp.assert_not_called()

    def test_falls_back_to_warp_when_shellomancy_declines(self):
        with mock.patch.object(fork_window.sys, "platform", "darwin"), \
             mock.patch.object(Path, "is_dir", return_value=True), \
             mock.patch.object(fork_window, "open_fork_in_shellomancy", return_value=False), \
             mock.patch.object(fork_window, "warp_installed", return_value=True), \
             mock.patch.object(fork_window, "_prune_old_configs"), \
             mock.patch.object(Path, "mkdir"), \
             mock.patch.object(fork_window, "_write_launch_config", return_value=Path("/x/claude-fork-1f8b2155.yaml")) as warp, \
             mock.patch.object(fork_window.subprocess, "Popen") as popen:
            result = fork_window.open_fork_window(self._input())
        self.assertEqual(result, Path("/x/claude-fork-1f8b2155.yaml"))
        warp.assert_called_once()
        popen.assert_called_once()

    def test_use_shellomancy_false_goes_straight_to_warp(self):
        with mock.patch.object(fork_window.sys, "platform", "darwin"), \
             mock.patch.object(Path, "is_dir", return_value=True), \
             mock.patch.object(fork_window, "open_fork_in_shellomancy") as tab, \
             mock.patch.object(fork_window, "warp_installed", return_value=False):
            self.assertIsNone(fork_window.open_fork_window(self._input(), use_shellomancy=False))
        tab.assert_not_called()

    def test_rejects_non_hex_session_ids(self):
        with mock.patch.object(fork_window.sys, "platform", "darwin"), \
             mock.patch.object(Path, "is_dir", return_value=True), \
             mock.patch.object(fork_window, "open_fork_in_shellomancy") as tab:
            for bad in ["1f8b215;", "$(rm -r)", "ABC"]:
                self.assertIsNone(fork_window.open_fork_window(self._input(bad + "-rest")))
        tab.assert_not_called()


if __name__ == "__main__":
    unittest.main()
