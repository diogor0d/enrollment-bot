"""Command line entry points. Manual authentication never reads credentials."""

from __future__ import annotations

import argparse
from http.client import HTTPConnection, HTTPException
from pathlib import Path
import sys
import threading
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
    subparsers.add_parser("serve", help="poll and serve the allowlisted management console")
    subparsers.add_parser("capture-auth", help="manually sign in in a headed browser and save storage state")
    subparsers.add_parser("health", help="check that the polling loop has made recent progress")
    subparsers.add_parser("health-service", help="check polling progress and the management console")
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
    if args.command in {"health", "health-service"}:
        try:
            state = AtomicState(settings.data_path).read()
            if state.get("manual_intervention"):
                return 1
            terminal_success = state.get("enrollment_status") == "success"
            last_cycle = int(state.get("last_cycle_epoch", 0))
        except (OSError, TypeError, ValueError, StateError):
            return 1
        stale_after = max(settings.poll_interval, settings.auth_retry_interval) + settings.jitter + 300
        if not terminal_success and (last_cycle <= 0 or time.time() - last_cycle > stale_after):
            return 1
        if args.command == "health-service":
            connection = HTTPConnection("127.0.0.1", settings.ui_port, timeout=2)
            try:
                connection.request("GET", "/healthz")
                response = connection.getresponse()
                response.read(1024)
                if response.status != 200:
                    return 1
            except (OSError, HTTPException):
                return 1
            finally:
                connection.close()
        return 0
    watcher = Watcher(settings)
    if args.command == "once":
        result = watcher.run_once()
        return 0 if result not in {"failure", "auth_required", "manual_intervention"} else 1
    if args.command == "serve":
        from .web import ManagementApp, create_server

        def check_now() -> str:
            deadline = time.monotonic() + 60
            while True:
                result = watcher.run_once()
                if result != "busy" or time.monotonic() >= deadline:
                    return result
                time.sleep(1)

        app = ManagementApp(settings, watcher.state, check_now)
        server = create_server(settings.ui_host, settings.ui_port, app)
        server_thread = threading.Thread(
            target=server.serve_forever,
            name="vacancy-watcher-console",
            daemon=True,
        )
        server_thread.start()
        watcher.logger.log("info", "management_console_started", port=settings.ui_port)
        try:
            watcher.run_forever()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
        return 0
    watcher.run_forever()
    return 0
