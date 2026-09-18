from pathlib import Path
import tempfile
import unittest

from vacancy_watcher.config import Settings
from vacancy_watcher.watcher import Watcher


class WatcherAuthenticationTests(unittest.TestCase):
    def test_submission_is_accepted_once_and_secrets_are_not_exposed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watcher = Watcher(
                Settings(
                    data_path=root / "state.json",
                    storage_state_path=root / "auth-state.json",
                )
            )
            watcher._set_auth_flow(
                "challenge",
                "data:image/png;base64,dGVzdA==",
                deadline=10**20,
            )

            self.assertFalse(watcher.submit_authentication("user", "password", ""))
            self.assertTrue(watcher.submit_authentication("user", "password", "answer"))
            self.assertFalse(watcher.submit_authentication("other", "secret", "answer"))

            snapshot = watcher.authentication_snapshot()
            self.assertEqual(snapshot["status"], "submitting")
            self.assertNotIn("user", repr(snapshot))
            self.assertNotIn("password", repr(snapshot))
            self.assertNotIn("answer", repr(snapshot))

    def test_submission_without_captcha_is_allowed_only_when_portal_did_not_show_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watcher = Watcher(
                Settings(
                    data_path=root / "state.json",
                    storage_state_path=root / "auth-state.json",
                )
            )
            watcher._set_auth_flow("challenge", None, deadline=10**20)

            self.assertTrue(watcher.submit_authentication("user", "password", ""))


if __name__ == "__main__":
    unittest.main()
