"""Structured stdout logging and optional generic JSON webhook notifications."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from typing import Any
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .state import AtomicState


class JsonLogger:
    def log(self, level: str, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
            **fields,
        }
        print(json.dumps(record, ensure_ascii=False, sort_keys=True), file=sys.stdout, flush=True)


class _RejectRedirects(HTTPRedirectHandler):
    """Keep a configured webhook from being redirected to another origin."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class Notifier:
    def __init__(self, webhook_url: str | None, state: AtomicState, logger: JsonLogger | None = None):
        self.webhook_url = webhook_url
        self.state = state
        self.logger = logger or JsonLogger()

    def notify_once(self, key: str, event: str, **fields: Any) -> bool:
        """Always log the event, but send a webhook only once for each state key."""

        self.logger.log("info", event, **fields)
        if not self.state.claim_notification(key):
            self.logger.log("debug", "notification_suppressed", notification_key=key)
            return False
        if self.webhook_url:
            body = json.dumps({"event": event, **fields}, ensure_ascii=False).encode("utf-8")
            request = Request(self.webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with build_opener(_RejectRedirects).open(request, timeout=10):
                    pass
            except (OSError, URLError):
                # The watcher remains useful from stdout and will not retry a notification storm.
                self.logger.log("warning", "webhook_delivery_failed", notification_key=key)
        return True
