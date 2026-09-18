"""One-shot and polling orchestration for the fail-closed watcher."""

from __future__ import annotations

from pathlib import Path
import random
import signal
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlparse

from .config import Settings
from .models import decide_target_class
from .notifier import JsonLogger, Notifier
from .portal import (
    PortalAssertionError,
    PortalAuthRequired,
    assert_course_detail,
    assert_list_url,
    assert_enrollment_form,
    authoritative_list_verification,
    class_snapshots,
    find_course_link,
    submit_once_and_verify,
)
from .state import (
    AtomicState,
    ExclusiveFileLock,
    LockHeldError,
    StateError,
    write_json_secure_atomic,
)


class Watcher:
    def __init__(self, settings: Settings, *, logger: JsonLogger | None = None):
        settings.validate()
        self.settings = settings
        self.logger = logger or JsonLogger()
        self.state = AtomicState(settings.data_path)
        self.notifier = Notifier(settings.webhook_url, self.state, self.logger)

    def _record_failure(self, kind: str) -> None:
        def mutate(state: dict[str, Any]) -> None:
            state["failure_count"] = int(state.get("failure_count", 0)) + 1

        state = self.state.update(mutate)
        count = int(state["failure_count"])
        if count >= 3:
            self.notifier.notify_once(f"failure:{kind}", "repeated_failure", failure_kind=kind, consecutive_failures=count)

    def _clear_failures(self) -> None:
        self.state.update(lambda state: state.__setitem__("failure_count", 0))

    def _auth_required(self) -> str:
        self.notifier.notify_once("auth_required", "auth_required")
        self._record_failure("auth_required")
        return "auth_required"

    def _save_storage_state(self, context: Any) -> None:
        path = Path(self.settings.storage_state_path)
        write_json_secure_atomic(path, context.storage_state())

    def _authenticated_page(self, page: Any) -> None:
        try:
            assert_list_url(page.url, self.settings)
        except PortalAuthRequired:
            raise
        try:
            if page.locator("input[type='password']").count() > 0:
                raise PortalAuthRequired("login form is visible")
        except PortalAuthRequired:
            raise
        except Exception:
            # A transient DOM failure is not evidence of authentication; later assertions decide.
            pass

    def run_once(self) -> str:
        lock_path = Path(self.settings.data_path).parent / ".vacancy-watcher-cycle.lock"
        try:
            with ExclusiveFileLock(lock_path):
                return self._run_once_locked()
        except LockHeldError:
            self.logger.log("warning", "watcher_cycle_already_running")
            return "busy"

    def _run_once_locked(self) -> str:
        try:
            state = self.state.read()
        except StateError:
            self.logger.log("error", "state_file_invalid")
            return "manual_intervention"
        state = self.state.update(lambda value: value.__setitem__("last_cycle_epoch", int(time.time())))
        if state.get("manual_intervention"):
            self.notifier.notify_once("manual_intervention_latched", "manual_intervention_required")
            return "manual_intervention"
        recovering_submission = state.get("enrollment_status") == "submitting"
        if not Path(self.settings.storage_state_path).is_file():
            return self._auth_required()

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._record_failure("playwright_missing")
            raise RuntimeError("Playwright 1.63.0 is required; install project dependencies")

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = None
                authenticated = False
                try:
                    context = browser.new_context(storage_state=str(self.settings.storage_state_path))
                    page = context.new_page()
                    page.goto(self.settings.list_url, wait_until="domcontentloaded")
                    self._authenticated_page(page)
                    authenticated = True
                    if recovering_submission:
                        try:
                            confirmed_after_restart = authoritative_list_verification(page, self.settings)
                        except Exception:
                            self.state.latch_manual_intervention("uncertain")
                            self.notifier.notify_once(
                                "enrollment:uncertain",
                                "enrollment_uncertain_after_restart",
                            )
                            return "manual_intervention"
                        if confirmed_after_restart:
                            self.state.update(lambda value: value.__setitem__("enrollment_status", "success"))
                            self.notifier.notify_once(
                                "enrollment:success",
                                "enrollment_success_after_restart",
                                class_name=self.settings.class_name,
                            )
                            self._clear_failures()
                            return "success"
                        self.state.latch_manual_intervention("uncertain")
                        self.notifier.notify_once("enrollment:uncertain", "enrollment_uncertain_after_restart")
                        return "manual_intervention"
                    course = find_course_link(page, self.settings)
                    detail_url = urljoin(self.settings.list_url, course.href)
                    detail = urlparse(detail_url)
                    if (
                        detail.scheme != "https"
                        or detail.hostname != self.settings.expected_host
                        or detail.port is not None
                        or detail.username is not None
                        or detail.password is not None
                        or detail.path != "/nonio/inscturmas/inscrever.do"
                        or detail.params
                        or detail.fragment
                    ):
                        raise PortalAssertionError("fresh course link origin/path is not exact")
                    page.goto(detail_url, wait_until="domcontentloaded")
                    assert_course_detail(page, self.settings)
                    snapshots = class_snapshots(page, self.settings)
                    decision = decide_target_class(
                        snapshots,
                        class_name=self.settings.class_name,
                        profile_alt=self.settings.profile_alt,
                        expected_class_id=self.settings.expected_class_id,
                    )
                    if decision.kind == "available":
                        status = "available"
                    elif decision.kind == "no_vacancy":
                        status = "unavailable"
                    else:
                        status = "unknown"
                    previous = self.state.transition_vacancy_status(status)
                    if status == "available" and previous != "available":
                        self.notifier.notify_once("vacancy:available", "vacancy_available", class_name=self.settings.class_name)
                    elif status == "unavailable" and previous == "available":
                        self.notifier.notify_once("vacancy:unavailable", "vacancy_unavailable", class_name=self.settings.class_name)
                    if decision.kind == "already_enrolled":
                        self.state.update(lambda value: value.__setitem__("enrollment_status", "success"))
                        self.notifier.notify_once("enrollment:already_enrolled", "already_enrolled", class_name=self.settings.class_name)
                        self._clear_failures()
                        return "success"
                    if decision.kind == "available" and self.settings.mode == "enroll":
                        if not self.settings.enrollment_gate:
                            self.logger.log("warning", "enrollment_gates_closed")
                            return "available"
                        assert_enrollment_form(page, self.settings)
                        self.state.mark_submission_started()
                        try:
                            confirmed = submit_once_and_verify(page, self.settings)
                        except Exception:
                            self.state.latch_manual_intervention("uncertain")
                            self.notifier.notify_once("enrollment:uncertain", "enrollment_uncertain")
                            return "manual_intervention"
                        if confirmed:
                            self.state.update(lambda value: value.__setitem__("enrollment_status", "success"))
                            self.notifier.notify_once("enrollment:success", "enrollment_success", class_name=self.settings.class_name)
                            self._clear_failures()
                            return "success"
                    self._clear_failures()
                    return status
                finally:
                    if context is not None and authenticated:
                        try:
                            self._save_storage_state(context)
                        except Exception:
                            self.logger.log("warning", "storage_state_refresh_failed")
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            self.logger.log("warning", "browser_context_close_failed")
                    try:
                        browser.close()
                    except Exception:
                        self.logger.log("warning", "browser_close_failed")
        except PortalAuthRequired:
            return self._auth_required()
        except Exception as exc:
            self._record_failure(type(exc).__name__)
            self.logger.log("error", "cycle_failed", failure_kind=type(exc).__name__)
            return "failure"

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop = stop_event or threading.Event()

        def request_stop(_signum: int, _frame: Any) -> None:
            stop.set()

        for signal_name in ("SIGTERM", "SIGINT"):
            signum = getattr(signal, signal_name, None)
            if signum is not None:
                try:
                    signal.signal(signum, request_stop)
                except ValueError:
                    pass
        while not stop.is_set():
            result = self.run_once()
            if result == "success" and self.settings.stop_after_success:
                self.logger.log("info", "watcher_idle_after_success")
                stop.wait()
                break
            base_delay = (
                self.settings.auth_retry_interval
                if result in {"auth_required", "manual_intervention"}
                else self.settings.poll_interval
            )
            delay = base_delay + random.uniform(0, self.settings.jitter)
            stop.wait(delay)
