"""Command line entry points. Manual authentication never reads credentials."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from .config import ConfigError, Settings
from .portal import PortalAssertionError, assert_list_url, find_course_link
from .state import AtomicState, StateError, write_json_secure_atomic
from .watcher import Watcher


def capture_auth(settings: Settings) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright 1.63.0 is required; install project dependencies", file=sys.stderr)
        return 2
    path = Path(settings.storage_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    print("A browser window will open. Log in manually there; this command never reads credentials.")
    print("When the authenticated InforEstudante page is ready, return here and press Enter.")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(settings.list_url, wait_until="domcontentloaded")
                input()
                page.goto(settings.list_url, wait_until="domcontentloaded")
                assert_list_url(page.url, settings)
                if page.locator("input[type='password']").count() > 0:
                    raise PortalAssertionError("login form is still visible")
                find_course_link(page, settings)
                write_json_secure_atomic(path, context.storage_state())
            finally:
                browser.close()
    except (PortalAssertionError, OSError) as exc:
        print(f"Authentication state was not saved: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("Authentication state saved. Treat this file as equivalent to a password and keep it outside Git.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vacancy-watcher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("once", help="run one headless authenticated observation")
    subparsers.add_parser("run", help="poll until stopped or confirmed enrollment")
    subparsers.add_parser("capture-auth", help="manually sign in in a headed browser and save storage state")
    subparsers.add_parser("health", help="check that the polling loop has made recent progress")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        settings.validate()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.command == "capture-auth":
        return capture_auth(settings)
    if args.command == "health":
        try:
            state = AtomicState(settings.data_path).read()
            if state.get("manual_intervention"):
                return 1
            if state.get("enrollment_status") == "success":
                return 0
            last_cycle = int(state.get("last_cycle_epoch", 0))
        except (OSError, TypeError, ValueError, StateError):
            return 1
        stale_after = max(settings.poll_interval, settings.auth_retry_interval) + settings.jitter + 300
        return 0 if last_cycle > 0 and time.time() - last_cycle <= stale_after else 1
    watcher = Watcher(settings)
    if args.command == "once":
        result = watcher.run_once()
        return 0 if result not in {"failure", "auth_required", "manual_intervention"} else 1
    watcher.run_forever()
    return 0
