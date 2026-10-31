"""utils.mqtt_publish against a real local mosquitto broker.

Run from the hooks dir: python3 -m unittest discover -s tests -p "test_mqtt_publish.py" -v
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import mqtt_publish  # noqa: E402

MOSQUITTO = shutil.which("mosquitto") or "/opt/homebrew/sbin/mosquitto"


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class MarkerMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.marker = mock.patch.object(mqtt_publish, "MARKER_DIR", Path(self.tmp.name))
        self.marker.start()

    def tearDown(self):
        self.marker.stop()
        self.tmp.cleanup()


@unittest.skipUnless(os.path.exists(MOSQUITTO) and shutil.which("mosquitto_sub"), "needs mosquitto")
class BrokerTest(MarkerMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.port = _free_port()
        self.broker = subprocess.Popen([MOSQUITTO, "-p", str(self.port)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        self.env = mock.patch.dict(os.environ, {"MADNESS_MQTT_HOST": "127.0.0.1",
                                                "MADNESS_MQTT_PORT": str(self.port)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.broker.terminate()
        self.broker.wait(5)
        super().tearDown()

    def test_retained_publish_is_read_back_by_get(self):
        self.assertTrue(mqtt_publish.publish("status/t/claude/hooks_config", {"a": 1}, retain=True))
        self.assertEqual(mqtt_publish.get("status/t/claude/hooks_config"), '{"a": 1}')

    def test_get_returns_none_without_a_retained_message(self):
        self.assertIsNone(mqtt_publish.get("status/t/nothing/here"))

    def test_plain_publish_reaches_a_subscriber(self):
        sub = subprocess.Popen(["mosquitto_sub", "-h", "127.0.0.1", "-p", str(self.port),
                                "-t", "status/t/activity/pre_tool", "-C", "1", "-W", "5"],
                               stdout=subprocess.PIPE, text=True)
        time.sleep(0.5)
        self.assertTrue(mqtt_publish.publish("status/t/activity/pre_tool", '{"tool": "Bash"}'))
        self.assertEqual(sub.communicate(timeout=10)[0].strip(), '{"tool": "Bash"}')

    def test_large_payload(self):
        big = "x" * 70000  # needs a 3-byte remaining-length
        self.assertTrue(mqtt_publish.publish("status/t/big", big, retain=True))
        self.assertEqual(mqtt_publish.get("status/t/big"), big)


class DownBrokerTest(MarkerMixin, unittest.TestCase):
    def test_unreachable_broker_fails_fast_and_backs_off(self):
        with mock.patch.dict(os.environ, {"MADNESS_MQTT_HOST": "127.0.0.1",
                                          "MADNESS_MQTT_PORT": str(_free_port())}):
            started = time.time()
            self.assertFalse(mqtt_publish.publish("status/t/x", "1"))
            self.assertTrue(mqtt_publish.down_marker().exists())
            with mock.patch.object(mqtt_publish.socket, "create_connection") as connect:
                self.assertFalse(mqtt_publish.publish("status/t/x", "1"))
                self.assertIsNone(mqtt_publish.get("status/t/x"))
                connect.assert_not_called()
            self.assertLess(time.time() - started, 2)

    def test_down_marker_is_per_broker(self):
        with mock.patch.dict(os.environ, {"MADNESS_MQTT_HOST": "127.0.0.1",
                                          "MADNESS_MQTT_PORT": str(_free_port())}):
            self.assertFalse(mqtt_publish.publish("status/t/x", "1"))
        with mock.patch.dict(os.environ, {"MADNESS_MQTT_HOST": "127.0.0.1", "MADNESS_MQTT_PORT": "9"}):
            self.assertFalse(mqtt_publish._broker_marked_down())


if __name__ == "__main__":
    unittest.main()
