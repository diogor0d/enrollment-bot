import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot


class ConfigurationTests(unittest.TestCase):
    def test_references_resolve_outside_project_working_directory(self):
        original_cwd = Path.cwd()
        os.chdir(tempfile.gettempdir())
        try:
            courses = bot.load_and_validate_configuration()
        finally:
            os.chdir(original_cwd)

        self.assertTrue(courses)
        self.assertTrue(all(Path(course).is_absolute() for course in courses))
        self.assertTrue(all(path.is_file() for path in bot.REQUIRED_UI_IMAGES))

    def test_action_region_starts_after_the_detected_label(self):
        region = bot.action_region_to_right((400, 300, 300, 30), 1920)
        self.assertEqual(region, (700, 296, 1220, 38))

    def test_class_enrollment_region_preserves_original_right_edge_crop(self):
        region = bot.class_enrollment_region((400, 300, 42, 26), 1920)
        self.assertEqual(region, (1700, 300, 220, 26))


class ModeTests(unittest.TestCase):
    def test_default_mode_is_dry_run(self):
        self.assertEqual(bot.parse_args([]).mode, "dry-run")

    def test_live_mode_is_explicit(self):
        self.assertEqual(bot.parse_args(["--mode", "live"]).mode, "live")

    def test_visual_mode_is_explicit(self):
        self.assertEqual(bot.parse_args(["--mode", "visual"]).mode, "visual")

    def test_visualized_lookup_hides_before_search_and_shows_capture(self):
        events = []

        class FakeViewport:
            def hide(self):
                events.append("hide")

            def show(self, screenshot, box, label, color):
                events.append(("show", screenshot, box, label, color))

        match = (10, 20, 30, 40)
        screenshot = object()
        original_viewport = bot.visual_viewport
        bot.visual_viewport = FakeViewport()
        try:
            with patch.object(
                bot.pyautogui,
                "locateOnScreen",
                side_effect=lambda *args, **kwargs: events.append("locate") or match,
            ), patch.object(
                bot.pyautogui,
                "screenshot",
                side_effect=lambda: events.append("screenshot") or screenshot,
            ), patch.object(bot.time, "sleep"):
                result = bot.locate_reference(
                    "reference.png", "Detected item", "#ffffff", confidence=0.9
                )
        finally:
            bot.visual_viewport = original_viewport

        self.assertEqual(result, match)
        self.assertEqual(events[0], "hide")
        self.assertEqual(events[1], "locate")
        self.assertEqual(events[2], "screenshot")
        self.assertEqual(
            events[3],
            ("show", screenshot, match, "Detected item", "#ffffff"),
        )

    @patch.object(bot.keyboard, "send")
    @patch.object(bot.pyautogui, "click")
    @patch.object(bot.pyautogui, "moveTo")
    @patch.object(bot.pyautogui, "center", return_value=(10, 20))
    def test_dry_run_skips_save_and_returns(
        self, center, move_to, click, keyboard_send
    ):
        bot.complete_enrollment(object(), True, "test choice")

        center.assert_called_once()
        move_to.assert_called_once_with(10, 20)
        click.assert_not_called()
        keyboard_send.assert_called_once_with("browser_back")

    @patch.object(bot.keyboard, "send")
    @patch.object(bot.pyautogui, "click")
    @patch.object(bot.pyautogui, "moveTo")
    @patch.object(bot.pyautogui, "center", return_value=(10, 20))
    def test_live_mode_clicks_save(
        self, center, move_to, click, keyboard_send
    ):
        bot.complete_enrollment(object(), False, "test choice")

        center.assert_called_once()
        move_to.assert_called_once_with(10, 20)
        click.assert_called_once_with()
        keyboard_send.assert_not_called()


class LiveEnrollmentFlowTests(unittest.TestCase):
    def setUp(self):
        self.original_running = bot.running
        self.original_courses = bot.cadeira_images
        bot.running = True
        bot.cadeira_images = [str(bot.CADEIRA_FOLDER / "a-sp.png")]

    def tearDown(self):
        bot.running = self.original_running
        bot.cadeira_images = self.original_courses

    def run_flow(self, matches):
        events = []

        def center(box):
            left, top, width, height = box
            return left + width // 2, top + height // 2

        with patch.object(bot, "locate_reference", side_effect=matches), patch.object(
            bot.pyautogui, "size", return_value=(1920, 1080)
        ), patch.object(
            bot.pyautogui,
            "center",
            side_effect=center,
        ), patch.object(
            bot.pyautogui,
            "moveTo",
            side_effect=lambda x, y: events.append(("move", x, y)),
        ), patch.object(
            bot.pyautogui,
            "click",
            side_effect=lambda: events.append(("click",)),
        ), patch.object(
            bot.pyautogui, "scroll"
        ), patch.object(
            bot.keyboard, "send"
        ) as keyboard_send, patch.object(
            bot.time, "sleep"
        ):
            bot.run_automation_sequence(dry_run=False)

        keyboard_send.assert_not_called()
        return events

    def assert_action_sequence(self, events, expected):
        position = 0
        for event in events:
            if position < len(expected) and event == expected[position]:
                position += 1
        self.assertEqual(position, len(expected), events)

    def test_preferred_live_flow_preserves_checkbox_then_save_clicks(self):
        open_button = (1700, 300, 78, 22)
        checkbox = (1750, 500, 20, 20)
        save_button = (1650, 900, 100, 30)
        matches = [
            (300, 200, 240, 20),  # enrollment list
            (300, 300, 310, 24),  # course
            open_button,
            (300, 400, 220, 30),  # class page
            (300, 500, 42, 26),  # preferred class
            checkbox,
            save_button,
        ]

        events = self.run_flow(matches)

        self.assert_action_sequence(
            events,
            [
                ("move", 1739, 311),
                ("click",),
                ("move", 1760, 510),
                ("click",),
                ("move", 1700, 915),
                ("click",),
            ],
        )

    def test_fallback_live_flow_preserves_checkbox_then_save_clicks(self):
        open_button = (1700, 300, 78, 22)
        fallback_checkbox = (1750, 540, 20, 20)
        save_button = (1650, 900, 100, 30)
        matches = [
            (300, 200, 240, 20),  # enrollment list
            (300, 300, 310, 24),  # course
            open_button,
            (300, 400, 220, 30),  # class page
            (300, 500, 42, 26),  # preferred class
            bot.pyautogui.ImageNotFoundException("preferred checkbox unavailable"),
            (300, 540, 42, 26),  # fallback class
            fallback_checkbox,
            save_button,
        ]

        events = self.run_flow(matches)

        self.assert_action_sequence(
            events,
            [
                ("move", 1739, 311),
                ("click",),
                ("move", 1760, 550),
                ("click",),
                ("move", 1700, 915),
                ("click",),
            ],
        )


class WorkerLifecycleTests(unittest.TestCase):
    def test_running_worker_prevents_overlapping_start(self):
        class FakeThread:
            def __init__(self):
                self.started = False

            def start(self):
                self.started = True

            def is_alive(self):
                return self.started

        fake_thread = FakeThread()
        with bot.state_lock:
            bot.running = False
            bot.automation_thread = None

        try:
            with patch.object(bot.threading, "Thread", return_value=fake_thread) as thread:
                bot.start_automation(True)
                bot.start_automation(True)

            thread.assert_called_once()
            self.assertTrue(bot.running)
        finally:
            with bot.state_lock:
                bot.running = False
                bot.automation_thread = None


if __name__ == "__main__":
    unittest.main()
