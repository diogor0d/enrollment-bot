import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from vacancy_watcher.cli import main
from vacancy_watcher.state import AtomicState


class CliTests(unittest.TestCase):
    class FakeHealthResponse:
        status = 200

        def read(self, _limit):
            return b'{"ok":true}'

    class FakeHealthConnection:
        def __init__(self, *_args, **_kwargs):
            self.requested = False

        def request(self, method, path):
            self.requested = (method, path) == ("GET", "/healthz")

        def getresponse(self):
            if not self.requested:
                raise OSError("health request was not sent")
            return CliTests.FakeHealthResponse()

        def close(self):
            return None

    def test_health_requires_recent_progress_and_rejects_manual_intervention(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            environment = {
                "DATA_PATH": str(state_path),
                "AUTH_RETRY_INTERVAL": "60",
                "POLL_INTERVAL": "10",
                "POLL_JITTER": "0",
            }
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(main(["health"]), 1)
                state = AtomicState(state_path)
                state.update(lambda value: value.__setitem__("last_cycle_epoch", int(time.time())))
                self.assertEqual(main(["health"]), 0)
                with patch("vacancy_watcher.cli.HTTPConnection", self.FakeHealthConnection):
                    self.assertEqual(main(["health-service"]), 0)
                state.latch_manual_intervention()
                self.assertEqual(main(["health"]), 1)

    def test_health_accepts_terminal_success_without_a_recent_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = AtomicState(state_path)
            state.update(lambda value: value.__setitem__("enrollment_status", "success"))
            with patch.dict(os.environ, {"DATA_PATH": str(state_path)}, clear=False):
                self.assertEqual(main(["health"]), 0)
                with patch("vacancy_watcher.cli.HTTPConnection", self.FakeHealthConnection):
                    self.assertEqual(main(["health-service"]), 0)


if __name__ == "__main__":
    unittest.main()
