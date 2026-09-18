"""Allowlisted management console for the vacancy watcher.

The module deliberately uses only the Python standard library.  A caller owns
the bind address and supplies a callback for ``check-now``; the callback is
always run on a background thread so a slow browser cycle cannot hold an HTTP
worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import ipaddress
import secrets
import ssl
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .config import ENROLLMENT_ACK, Settings
from .state import AtomicState


CSRF_COOKIE = "vw_csrf"
MAX_FORM_BYTES = 8192


@dataclass
class ManagementApp:
    """Dependencies shared by each short-lived HTTP handler."""

    settings: Settings
    state: AtomicState
    check_now: Callable[[], Any] | None = None
    auth_start: Callable[[], bool] | None = None
    auth_snapshot: Callable[[], dict[str, Any]] | None = None
    auth_submit: Callable[[str, str, str], bool] | None = None

    def __post_init__(self) -> None:
        self._operation_lock = threading.Lock()
        self._check_running = False

    @property
    def check_running(self) -> bool:
        with self._operation_lock:
            return self._check_running

    def start_check_now(self) -> bool:
        """Start a supplied check callback without making the HTTP request wait."""

        if self.check_now is None:
            return False
        with self._operation_lock:
            if self._check_running or self.authentication_snapshot()["status"] in {
                "preparing",
                "challenge",
                "submitting",
            }:
                return False
            self._check_running = True

        def run() -> None:
            try:
                self.check_now()
            except Exception:
                # The watcher callback owns its error reporting.  The console
                # must never turn callback failures into an HTTP-thread crash.
                pass
            finally:
                with self._operation_lock:
                    self._check_running = False

        thread = threading.Thread(target=run, name="vacancy-watcher-check", daemon=True)
        thread.start()
        return True

    def authentication_snapshot(self) -> dict[str, Any]:
        """Return only non-secret state from the active portal login flow."""

        if self.auth_snapshot is None:
            return {"status": "idle", "captcha": None, "expires_in": 0}
        try:
            snapshot = self.auth_snapshot()
        except Exception:
            return {"status": "failed", "captcha": None, "expires_in": 0}
        return {
            "status": str(snapshot.get("status", "failed")),
            "captcha": snapshot.get("captcha"),
            "expires_in": max(0, int(snapshot.get("expires_in", 0))),
        }

    def start_authentication(self) -> bool:
        """Prepare a browser session before any credentials are collected."""

        if self.auth_start is None:
            return False
        with self._operation_lock:
            if self._check_running:
                return False
            return self.auth_start()

    def submit_authentication(self, username: str, password: str, captcha: str) -> bool:
        """Pass credentials and the user-solved CAPTCHA to the waiting session."""

        if self.auth_submit is None:
            return False
        return self.auth_submit(username, password, captcha)


class ManagementServer(ThreadingHTTPServer):
    """Threading HTTP server carrying one :class:`ManagementApp`."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], app: ManagementApp):
        self.app = app
        super().__init__(server_address, ManagementHandler)


def create_server(host: str, port: int, app: ManagementApp) -> ManagementServer:
    """Create a server on loopback or the fixed container wildcard address.

    ``0.0.0.0`` is permitted only inside the container. Compose controls the
    host-interface publication, while request host/client allowlists provide a
    second enforcement layer.
    """

    if host != "0.0.0.0" and host.lower() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("management console must bind to a loopback address")
        except ValueError as exc:
            if str(exc) == "management console must bind to a loopback address":
                raise
            raise ValueError("management console must bind to a loopback address") from exc
    server = ManagementServer((host, port), app)
    if app.settings.tls_enabled:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(app.settings.tls_cert_path, app.settings.tls_key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _state_value(state: dict[str, Any], key: str, default: Any) -> Any:
    value = state.get(key, default)
    return default if value is None else value


def _utc_timestamp(epoch: Any) -> str:
    try:
        stamp = float(epoch)
    except (TypeError, ValueError):
        return "Not recorded"
    if stamp <= 0:
        return "Not recorded"
    try:
        return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return "Not recorded"


def _status_label(value: Any) -> str:
    return {
        "available": "Vacancy available",
        "unavailable": "No vacancy",
        "unknown": "Awaiting a verified result",
    }.get(str(value), "Awaiting a verified result")


def _page_html(settings: Settings, state: dict[str, Any], csrf_token: str, message: str = "") -> str:
    """Render the complete dashboard with every dynamic value escaped."""

    monitoring = bool(_state_value(state, "monitoring_enabled", True))
    armed = bool(_state_value(state, "enrollment_armed", False))
    manual = bool(_state_value(state, "manual_intervention", False))
    vacancy = str(_state_value(state, "vacancy_status", "unknown"))
    enrollment = str(_state_value(state, "enrollment_status", "idle"))
    auth_flow = state.get("auth_flow", {})
    auth_flow_status = str(auth_flow.get("status", "idle"))
    auth_status = str(_state_value(state, "auth_status", "unknown"))
    last_result = str(_state_value(state, "last_result", "never_run"))
    failures = _state_value(state, "failure_count", 0)
    last_cycle = _utc_timestamp(_state_value(state, "last_cycle_epoch", 0))
    checked_text = "Check in progress" if bool(state.get("check_running", False)) else last_cycle
    gate = bool(settings.enrollment_gate)
    message_html = f'<p class="notice" role="status">{_escape(message)}</p>' if message else ""
    monitoring_label = "Monitoring active" if monitoring else "Monitoring paused"
    monitoring_class = "good" if monitoring else "muted"
    safety_label = "Enrollment armed" if armed else "Enrollment disarmed"
    safety_class = "caution" if armed else "muted"
    if manual:
        safety_label = "Manual intervention required"
        safety_class = "danger"
    if auth_flow_status == "challenge":
        captcha = str(auth_flow.get("captcha") or "")
        captcha_fields = ""
        captcha_explanation = "No CAPTCHA was requested for this portal session."
        if captcha:
            captcha_fields = f"""
          <div class="captcha"><img src="{_escape(captcha)}" alt="University CAPTCHA challenge"></div>
          <label for="portal-captcha">CAPTCHA response</label>
          <input id="portal-captcha" name="captcha" type="text" autocomplete="off" maxlength="256" required>"""
            captcha_explanation = "Solve the displayed university CAPTCHA before submitting."
        auth_control = f"""
        <form method="post" action="/auth/submit">
          <input type="hidden" name="csrf" value="{_escape(csrf_token)}">
          {captcha_fields}
          <label for="portal-username">University username</label>
          <input id="portal-username" name="username" type="text" autocomplete="username" maxlength="256" required>
          <label for="portal-password">University password</label>
          <input id="portal-password" name="password" type="password" autocomplete="current-password" maxlength="1024" required>
          <button type="submit">Connect university account</button>
        </form>
        <p class="fine">{captcha_explanation} This session expires in about {_escape(auth_flow.get('expires_in', 0))} seconds. Submitted values are handed once to the waiting browser and are not written to disk.</p>"""
    elif auth_flow_status in {"preparing", "submitting"}:
        label = "Preparing secure login…" if auth_flow_status == "preparing" else "Verifying university login…"
        auth_control = f'<button type="button" disabled>{label}</button><p class="fine">Refresh this page shortly. No credentials have been retained by the console.</p>'
    else:
        label = "Start new university login" if auth_flow_status in {"failed", "valid"} else "Prepare university login"
        auth_control = f"""
        <form method="post" action="/auth/start">
          <input type="hidden" name="csrf" value="{_escape(csrf_token)}">
          <button type="submit" {'disabled' if not settings.tls_enabled else ''}>{label}</button>
        </form>
        <p class="fine">The watcher first opens the fixed university login page. If the portal requests a CAPTCHA, it is shown here for you to solve. Authentication is accepted only over HTTPS.</p>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Vacancy watcher · UC operations console</title>
  <style>
    :root {{
      --paper: #f7f9fc; --surface: #ffffff; --ink: #182235; --slate: #5d6b83;
      --line: #d8e0ec; --cobalt: #2457d6; --cobalt-dark: #17398e;
      --green: #176a4b; --green-wash: #e7f4ee; --amber: #9b591c;
      --amber-wash: #fff2e2; --red: #a42c35; --red-wash: #fdebed;
      --shadow: 0 18px 40px rgba(24, 34, 53, .08);
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--paper); }}
    body {{ margin: 0; color: var(--ink); background: var(--paper); font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; line-height: 1.5; }}
    a, button, input {{ font: inherit; }}
    .wrap {{ width: min(1120px, calc(100% - 2rem)); margin: 0 auto; }}
    .top {{ padding: 3.5rem 0 2.25rem; display: flex; justify-content: space-between; gap: 2rem; align-items: flex-end; }}
    .eyebrow {{ margin: 0 0 .75rem; color: var(--cobalt); text-transform: uppercase; letter-spacing: .14em; font-size: .72rem; font-weight: 800; }}
    h1 {{ max-width: 720px; margin: 0; font-family: ui-serif, Georgia, serif; font-size: clamp(2.5rem, 7vw, 5.5rem); line-height: .94; letter-spacing: -.055em; font-weight: 600; }}
    .dek {{ max-width: 560px; margin: 1.3rem 0 0; color: var(--slate); font-size: 1.05rem; }}
    .stamp {{ color: var(--slate); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .75rem; text-align: right; white-space: nowrap; }}
    .rail {{ display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow); margin-bottom: 1.5rem; }}
    .rail-step {{ min-height: 90px; padding: 1rem 1.25rem; border-right: 1px solid var(--line); position: relative; }}
    .rail-step:last-child {{ border-right: 0; }}
    .rail-step::before {{ content: ""; display: block; width: 2.4rem; height: 3px; margin-bottom: .75rem; background: var(--line); }}
    .rail-step.active::before {{ background: var(--cobalt); }}
    .rail-step.warn::before {{ background: var(--amber); }}
    .rail-step strong {{ display: block; font-size: .8rem; text-transform: uppercase; letter-spacing: .08em; }}
    .rail-step span {{ color: var(--slate); font-size: .84rem; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(300px, .8fr); gap: 1.5rem; padding-bottom: 3rem; }}
    .card {{ background: var(--surface); border: 1px solid var(--line); box-shadow: var(--shadow); padding: 1.5rem; }}
    .card h2 {{ margin: 0 0 .35rem; font-family: ui-serif, Georgia, serif; font-size: 1.6rem; font-weight: 600; letter-spacing: -.02em; }}
    .card-intro {{ color: var(--slate); margin: 0 0 1.4rem; font-size: .92rem; }}
    .hero-status {{ border-top: 4px solid var(--cobalt); padding-top: 1.15rem; margin-bottom: 1.6rem; }}
    .hero-status .label {{ color: var(--slate); font-size: .78rem; text-transform: uppercase; letter-spacing: .1em; font-weight: 800; }}
    .hero-status .value {{ margin-top: .15rem; font-family: ui-serif, Georgia, serif; font-size: clamp(1.8rem, 4vw, 3rem); line-height: 1.05; }}
    .details {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: .75rem; }}
    .detail {{ padding: .9rem; background: #f2f5fa; border-left: 3px solid var(--line); }}
    .detail .k {{ display: block; color: var(--slate); font-size: .73rem; text-transform: uppercase; letter-spacing: .09em; font-weight: 800; }}
    .detail .v {{ display: block; margin-top: .25rem; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .86rem; overflow-wrap: anywhere; }}
    .status-list {{ list-style: none; padding: 0; margin: 0; display: grid; gap: .65rem; }}
    .status-list li {{ display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding-bottom: .65rem; border-bottom: 1px solid var(--line); font-size: .9rem; }}
    .pill {{ display: inline-flex; align-items: center; border-radius: 999px; padding: .25rem .6rem; font-size: .75rem; font-weight: 800; white-space: nowrap; }}
    .good {{ color: var(--green); background: var(--green-wash); }} .muted {{ color: var(--slate); background: #eef1f5; }}
    .caution {{ color: var(--amber); background: var(--amber-wash); }} .danger {{ color: var(--red); background: var(--red-wash); }}
    .actions {{ display: grid; gap: .65rem; margin-top: 1.25rem; }}
    form {{ margin: 0; }}
    button {{ width: 100%; cursor: pointer; border: 1px solid var(--cobalt); background: var(--cobalt); color: white; padding: .7rem .85rem; font-weight: 750; border-radius: .35rem; }}
    button:hover {{ background: var(--cobalt-dark); border-color: var(--cobalt-dark); }}
    button:disabled, input:disabled {{ cursor: not-allowed; opacity: .52; }}
    button.secondary {{ color: var(--cobalt-dark); background: white; }}
    button.danger-action {{ color: var(--red); border-color: #e5afb5; background: white; }}
    label {{ display: block; color: var(--slate); font-size: .8rem; font-weight: 700; margin: 1rem 0 .35rem; }}
    input[type=text], input[type=password] {{ width: 100%; border: 1px solid #aebbd0; border-radius: .35rem; padding: .65rem .7rem; color: var(--ink); background: white; }}
    .notice {{ background: var(--green-wash); border: 1px solid #b7dfcc; color: var(--green); padding: .75rem 1rem; margin: 0 0 1rem; font-size: .9rem; }}
    .warning {{ background: var(--amber-wash); border: 1px solid #eed2b0; color: #77451d; padding: .8rem 1rem; margin-top: 1rem; font-size: .88rem; }}
    .captcha {{ margin: 1rem 0 .25rem; padding: .75rem; border: 1px solid var(--line); background: #f2f5fa; text-align: center; }}
    .captcha img {{ display: inline-block; max-width: 100%; height: auto; }}
    .fine {{ color: var(--slate); font-size: .78rem; margin: 1rem 0 0; }}
    :focus-visible {{ outline: 3px solid #6f96ff; outline-offset: 3px; }}
    @media (max-width: 760px) {{ .top {{ display: block; padding-top: 2.5rem; }} .stamp {{ text-align: left; margin-top: 1.25rem; }} .grid {{ grid-template-columns: 1fr; }} .rail-step {{ min-height: 104px; padding: .8rem; }} .details {{ grid-template-columns: 1fr; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: .001ms !important; }} }}
  </style>
</head>
<body>
  <main class="wrap">
    <header class="top">
      <div>
        <p class="eyebrow">Universidade de Coimbra · operations console</p>
        <h1>Watch the opening.</h1>
        <p class="dek">A quiet control surface for one course, one target class, and a clearly bounded enrollment path.</p>
      </div>
      <div class="stamp">private console<br>allowlisted / no external assets</div>
    </header>
    {message_html}
    <section class="rail" aria-label="Safety rail">
      <div class="rail-step active"><strong>01 · Observe</strong><span>Read the verified course state.</span></div>
      <div class="rail-step {"active" if monitoring else "warn"}"><strong>02 · Control</strong><span>{_escape(monitoring_label)}.</span></div>
      <div class="rail-step {"warn" if armed or manual else ""}"><strong>03 · Enroll</strong><span>{_escape(safety_label)}.</span></div>
    </section>
    <div class="grid">
      <section class="card" aria-labelledby="course-heading">
        <div class="hero-status"><span class="label">Current target state</span><div class="value">{_escape(_status_label(vacancy))}</div></div>
        <h2 id="course-heading">{_escape(settings.course_title)}</h2>
        <p class="card-intro">The watcher follows the live course link and evaluates the acknowledged target only.</p>
        <div class="details">
          <div class="detail"><span class="k">Course code</span><span class="v">{_escape(settings.course_code)}</span></div>
          <div class="detail"><span class="k">Target class</span><span class="v">{_escape(settings.class_name)} / { _escape(settings.profile_alt) }</span></div>
          <div class="detail"><span class="k">Last cycle</span><span class="v">{_escape(checked_text)}</span></div>
          <div class="detail"><span class="k">Consecutive failures</span><span class="v">{_escape(failures)}</span></div>
          <div class="detail"><span class="k">Authentication</span><span class="v">{_escape(auth_status)}</span></div>
          <div class="detail"><span class="k">Last result</span><span class="v">{_escape(last_result)}</span></div>
        </div>
        {f'<div class="warning" role="alert">Manual intervention is required before any enrollment action can continue.</div>' if manual else ''}
      </section>
      <aside class="card" aria-labelledby="controls-heading">
        <h2 id="controls-heading">Controls</h2>
        <p class="card-intro">Every change is explicit. The console does not display credentials or connection details.</p>
        <ul class="status-list">
          <li><span>Monitoring</span><span class="pill {monitoring_class}">{_escape(monitoring_label)}</span></li>
          <li><span>Portal authentication</span><span class="pill {'good' if auth_status == 'valid' else 'caution'}">{_escape(auth_status)}</span></li>
          <li><span>Enrollment state</span><span class="pill {safety_class}">{_escape(safety_label)}</span></li>
          <li><span>Last enrollment result</span><span class="pill muted">{_escape(enrollment)}</span></li>
        </ul>
        {auth_control}
        <div class="actions">
          <form method="post" action="/{'pause' if monitoring else 'resume'}"><input type="hidden" name="csrf" value="{_escape(csrf_token)}"><button class="secondary" type="submit">{'Pause monitoring' if monitoring else 'Resume monitoring'}</button></form>
          <form method="post" action="/check-now"><input type="hidden" name="csrf" value="{_escape(csrf_token)}"><button type="submit" {'disabled' if not monitoring else ''}>Check now</button></form>
          <form method="post" action="/{'disarm' if armed else 'arm'}">
            <input type="hidden" name="csrf" value="{_escape(csrf_token)}">
            {'' if armed else f'<label for="ack">Acknowledgement phrase</label><input id="ack" name="ack" type="text" inputmode="text" autocomplete="off" placeholder="course:class:id" {"disabled" if not monitoring or not gate else ""}>'}
            <button class="{'danger-action' if armed else ''}" type="submit" {'disabled' if not armed and (not monitoring or not gate) else ''}>{'Disarm enrollment' if armed else 'Arm enrollment'}</button>
          </form>
        </div>
        <p class="fine">Enrollment gate: {'open' if gate else 'closed'} · the server still requires the exact acknowledgement phrase.</p>
      </aside>
    </div>
  </main>
</body>
</html>"""


class ManagementHandler(BaseHTTPRequestHandler):
    server_version = "VacancyWatcherConsole/1.0"

    @property
    def app(self) -> ManagementApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: Any) -> None:
        # Do not emit request URLs or form fields, which could contain tokens.
        return

    def _security_headers(self) -> dict[str, str]:
        headers = {
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        }
        if self.app.settings.tls_enabled:
            headers["Strict-Transport-Security"] = "max-age=31536000"
        return headers

    def _send(self, status: int, body: str, *, content_type: str = "text/html; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        for name, value in self._security_headers().items():
            self.send_header(name, value)
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _csrf_token(self) -> tuple[str, bool]:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except (ValueError, CookieError):
            cookies = SimpleCookie()
        morsel = cookies.get(CSRF_COOKIE)
        if morsel and len(morsel.value) >= 32:
            return morsel.value, False
        return secrets.token_urlsafe(32), True

    def _set_cookie(self, token: str) -> str:
        cookie = SimpleCookie()
        cookie[CSRF_COOKIE] = token
        cookie[CSRF_COOKIE]["path"] = "/"
        cookie[CSRF_COOKIE]["secure"] = self.app.settings.tls_enabled
        cookie[CSRF_COOKIE]["httponly"] = True
        cookie[CSRF_COOKIE]["samesite"] = "Strict"
        return cookie[CSRF_COOKIE].OutputString()

    def _allowed_host(self) -> tuple[str, int] | None:
        host_header = self.headers.get("Host", "")
        if not host_header:
            return None
        try:
            parsed = urlsplit(f"http://{host_header}")
            port = parsed.port or (443 if self.app.settings.tls_enabled else 80)
        except ValueError:
            return None
        if (
            parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        hostname = parsed.hostname.lower()
        if hostname == "localhost":
            return (hostname, port) if hostname in self.app.settings.ui_allowed_hosts else None
        try:
            hostname = str(ipaddress.ip_address(hostname))
        except ValueError:
            return None
        return (hostname, port) if hostname in self.app.settings.ui_allowed_hosts else None

    def _allowed_client(self) -> bool:
        try:
            client = str(ipaddress.ip_address(self.client_address[0]))
        except ValueError:
            return False
        return client in self.app.settings.ui_allowed_clients

    def _same_origin(self) -> bool:
        expected_host = self._allowed_host()
        reference = self.headers.get("Origin") or self.headers.get("Referer")
        if expected_host is None:
            return False
        if not reference or reference == "null":
            return self.headers.get("Sec-Fetch-Site", "").lower() == "same-origin"
        try:
            actual = urlsplit(reference)
        except ValueError:
            return False
        if actual.hostname is None:
            return False
        try:
            actual_port = actual.port or (443 if actual.scheme == "https" else 80)
        except ValueError:
            return False
        expected_scheme = "https" if self.app.settings.tls_enabled else "http"
        return (
            actual.scheme == expected_scheme
            and actual.hostname.lower() == expected_host[0]
            and actual_port == expected_host[1]
        )

    def _parse_form(self) -> dict[str, str]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid content length") from None
        if length < 0 or length > MAX_FORM_BYTES:
            raise ValueError("form too large")
        raw = self.rfile.read(length)
        values = parse_qs(raw.decode("utf-8", errors="strict"), keep_blank_values=True, max_num_fields=8)
        return {key: entries[0] for key, entries in values.items() if entries}

    def _state(self) -> dict[str, Any]:
        return self.app.state.read()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._allowed_client() or self._allowed_host() is None:
            self._send(HTTPStatus.FORBIDDEN, "Management access denied.")
            return
        path = urlsplit(self.path).path
        if path == "/healthz":
            try:
                self._state()
            except Exception:
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, '{"ok":false}', content_type="application/json")
                return
            self._send(HTTPStatus.OK, '{"ok":true}', content_type="application/json")
            return
        if path != "/":
            self._send(HTTPStatus.NOT_FOUND, "Not found")
            return
        token, new_cookie = self._csrf_token()
        try:
            state = self._state()
        except Exception:
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, "State is unavailable.")
            return
        state["check_running"] = self.app.check_running
        state["auth_flow"] = self.app.authentication_snapshot()
        message = parse_qs(urlsplit(self.path).query).get("message", [""])[0]
        headers = {"Set-Cookie": self._set_cookie(token)} if new_cookie else None
        self._send(HTTPStatus.OK, _page_html(self.app.settings, state, token, message=message), headers=headers)

    def _post_error(self, status: int, message: str) -> None:
        self._send(status, f"<p>{_escape(message)}</p>")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._allowed_client() or self._allowed_host() is None:
            self._post_error(HTTPStatus.FORBIDDEN, "Management access denied.")
            return
        path = urlsplit(self.path).path
        if path not in {"/pause", "/resume", "/check-now", "/auth/start", "/auth/submit", "/arm", "/disarm"}:
            self._post_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        if not self._same_origin():
            self._post_error(HTTPStatus.FORBIDDEN, "Same-origin validation failed.")
            return
        try:
            form = self._parse_form()
        except (UnicodeDecodeError, ValueError):
            self._post_error(HTTPStatus.BAD_REQUEST, "Invalid form.")
            return
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except (ValueError, CookieError):
            cookies = SimpleCookie()
        cookie = cookies.get(CSRF_COOKIE)
        token = form.get("csrf", "")
        if cookie is None or not secrets.compare_digest(cookie.value, token) or len(token) < 32:
            self._post_error(HTTPStatus.FORBIDDEN, "CSRF validation failed.")
            return

        try:
            if path == "/pause":
                self.app.state.set_monitoring_enabled(False)
                message = "Monitoring paused."
            elif path == "/resume":
                self.app.state.set_monitoring_enabled(True)
                message = "Monitoring resumed."
            elif path == "/check-now":
                if not self._state().get("monitoring_enabled", True):
                    self._post_error(HTTPStatus.CONFLICT, "Resume monitoring before requesting a check.")
                    return
                if not self.app.start_check_now():
                    self._post_error(HTTPStatus.CONFLICT, "A check is already running or no callback is configured.")
                    return
                message = "Check started in the background."
            elif path == "/auth/start":
                if not self.app.settings.tls_enabled:
                    self._post_error(HTTPStatus.UPGRADE_REQUIRED, "Authentication requires HTTPS.")
                    return
                if not self.app.start_authentication():
                    self._post_error(HTTPStatus.CONFLICT, "Authentication or a portal check is already running.")
                    return
                message = "Secure university login is being prepared. Refresh shortly to see the challenge."
            elif path == "/auth/submit":
                if not self.app.settings.tls_enabled:
                    self._post_error(HTTPStatus.UPGRADE_REQUIRED, "Authentication requires HTTPS.")
                    return
                username = form.pop("username", "")
                password = form.pop("password", "")
                captcha = form.pop("captcha", "")
                challenge = self.app.authentication_snapshot()
                captcha_required = bool(challenge.get("captcha"))
                if (
                    not username
                    or not password
                    or (captcha_required and not captcha)
                    or len(username) > 256
                    or len(password) > 1024
                    or len(captcha) > 256
                ):
                    self._post_error(
                        HTTPStatus.BAD_REQUEST,
                        "Username and password are required; the CAPTCHA response is required when shown.",
                    )
                    return
                if not self.app.submit_authentication(username, password, captcha):
                    self._post_error(HTTPStatus.CONFLICT, "The login challenge is absent, expired, or already submitted.")
                    return
                username = ""
                password = ""
                captcha = ""
                message = "University login submitted. Refresh shortly to see the verified result."
            elif path == "/disarm":
                self.app.state.set_enrollment_armed(False)
                message = "Enrollment disarmed."
            else:
                if not secrets.compare_digest(form.get("ack", ""), ENROLLMENT_ACK):
                    self._post_error(HTTPStatus.FORBIDDEN, "The acknowledgement phrase is not exact.")
                    return
                if not self.app.settings.enrollment_gate:
                    self._post_error(HTTPStatus.FORBIDDEN, "The enrollment gate is closed.")
                    return
                current = self._state()
                if not current.get("monitoring_enabled", True):
                    self._post_error(HTTPStatus.CONFLICT, "Resume monitoring before arming enrollment.")
                    return
                if current.get("manual_intervention"):
                    self._post_error(HTTPStatus.CONFLICT, "Manual intervention is required before arming.")
                    return
                self.app.state.set_enrollment_armed(True)
                message = "Enrollment armed."
        except Exception:
            self._post_error(HTTPStatus.SERVICE_UNAVAILABLE, "The state could not be updated safely.")
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        for name, value in self._security_headers().items():
            self.send_header(name, value)
        self.send_header("Location", "/?message=" + message.replace(" ", "%20"))
        self.send_header("Content-Length", "0")
        self.end_headers()
