import os
import unittest
from unittest.mock import patch

from vacancy_watcher.config import ConfigError, ENROLLMENT_ACK, Settings


class ConfigTests(unittest.TestCase):
    def test_enrollment_requires_all_exact_gates(self):
        base = Settings(mode="enroll", enable_enrollment=True, acknowledgement=ENROLLMENT_ACK)
        self.assertTrue(base.enrollment_gate)
        self.assertFalse(Settings(mode="notify", enable_enrollment=True, acknowledgement=ENROLLMENT_ACK).enrollment_gate)
        self.assertFalse(Settings(mode="enroll", enable_enrollment=False, acknowledgement=ENROLLMENT_ACK).enrollment_gate)
        self.assertFalse(Settings(mode="enroll", enable_enrollment=True, acknowledgement=ENROLLMENT_ACK + " ").enrollment_gate)

    def test_poll_interval_is_enforced(self):
        with patch.dict(os.environ, {"POLL_INTERVAL": "1", "AUTH_RETRY_INTERVAL": "1"}, clear=False):
            settings = Settings.from_env()
            self.assertEqual(settings.poll_interval, 10.0)
            self.assertEqual(settings.auth_retry_interval, 60.0)

    def test_programmatic_configuration_cannot_change_target_identity(self):
        with self.assertRaises(ConfigError):
            Settings(expected_class_id="different").validate()
        with self.assertRaises(ConfigError):
            Settings(mode="observe").validate()
        with self.assertRaises(ConfigError):
            Settings(ui_host="127.0.0.1").validate()

    def test_management_allowlists_accept_only_literal_addresses(self):
        with patch.dict(
            os.environ,
            {
                "UI_ALLOWED_HOSTS": "192.168.1.199",
                "UI_ALLOWED_CLIENTS": "192.168.1.200",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        settings.validate()
        self.assertIn("192.168.1.199", settings.ui_allowed_hosts)
        self.assertIn("192.168.1.200", settings.ui_allowed_clients)
        self.assertIn("127.0.0.1", settings.ui_allowed_hosts)
        self.assertIn("127.0.0.1", settings.ui_allowed_clients)

        with patch.dict(os.environ, {"UI_ALLOWED_CLIENTS": "desktop.internal"}, clear=True):
            with self.assertRaises(ConfigError):
                Settings.from_env()

    def test_webhook_requires_plain_https_default_port(self):
        Settings(webhook_url="https://hooks.example.test/path").validate()
        invalid_values = (
            "http://hooks.example.test/path",
            "https://user:secret@hooks.example.test/path",
            "https://hooks.example.test:8443/path",
            "https://hooks.example.test:invalid/path",
            "https://hooks.example.test/path#fragment",
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                Settings(webhook_url=value).validate()
