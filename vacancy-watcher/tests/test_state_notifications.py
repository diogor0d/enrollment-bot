import json
from pathlib import Path
import tempfile
import unittest

from vacancy_watcher.notifier import JsonLogger, Notifier
from vacancy_watcher.state import AtomicState, ExclusiveFileLock, LockHeldError


class RecordingLogger(JsonLogger):
    def __init__(self):
        self.events = []

    def log(self, level, event, **fields):
        self.events.append((level, event, fields))


class StateNotificationTests(unittest.TestCase):
    def test_cycle_lock_rejects_a_second_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "cycle.lock"
            with ExclusiveFileLock(lock_path):
                with self.assertRaises(LockHeldError):
                    with ExclusiveFileLock(lock_path):
                        self.fail("a second lock owner must not enter")

    def test_atomic_state_and_notification_dedupe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = AtomicState(path)
            logger = RecordingLogger()
            notifier = Notifier(None, state, logger)
            self.assertTrue(notifier.notify_once("vacancy:available", "vacancy_available", class_name="PL3"))
            self.assertFalse(notifier.notify_once("vacancy:available", "vacancy_available", class_name="PL3"))
            self.assertEqual(len(state.read()["notifications"]), 1)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 1)

    def test_ambiguity_latches_manual_intervention(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AtomicState(Path(directory) / "state.json")
            state.latch_manual_intervention()
            value = state.read()
            self.assertTrue(value["manual_intervention"])
            self.assertEqual(value["enrollment_status"], "uncertain")
            self.assertTrue(state.claim_notification("enrollment:uncertain"))
            self.assertFalse(state.claim_notification("enrollment:uncertain"))

    def test_vacancy_transition_rearms_later_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AtomicState(Path(directory) / "state.json")
            self.assertEqual(state.transition_vacancy_status("available"), "unknown")
            self.assertTrue(state.claim_notification("vacancy:available"))
            self.assertEqual(state.transition_vacancy_status("unavailable"), "available")
            self.assertNotIn("vacancy:available", state.read()["notifications"])
            self.assertTrue(state.claim_notification("vacancy:unavailable"))
            self.assertEqual(state.transition_vacancy_status("available"), "unavailable")
            self.assertNotIn("vacancy:unavailable", state.read()["notifications"])
            self.assertTrue(state.claim_notification("vacancy:available"))

    def test_pause_disarms_and_runtime_arm_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AtomicState(Path(directory) / "state.json")
            state.set_enrollment_armed(True)
            self.assertTrue(state.consume_enrollment_arm())
            self.assertFalse(state.consume_enrollment_arm())
            state.set_enrollment_armed(True)
            state.set_monitoring_enabled(False)
            value = state.read()
            self.assertFalse(value["monitoring_enabled"])
            self.assertFalse(value["enrollment_armed"])
