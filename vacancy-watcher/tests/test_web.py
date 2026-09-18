import http.client
import re
from pathlib import Path
import tempfile
import threading
import time
import unittest

from vacancy_watcher.config import ENROLLMENT_ACK, Settings
from vacancy_watcher.state import AtomicState
from vacancy_watcher.web import ManagementApp, create_server


class WebFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = AtomicState(Path(self.temp.name) / "state.json")
        self.settings = Settings(
            mode="enroll",
            enable_enrollment=True,
            acknowledgement=ENROLLMENT_ACK,
            data_path=Path(self.temp.name) / "state.json",
            storage_state_path=Path(self.temp.name) / "auth-state.json",
            webhook_url="https://hooks.example.invalid/private",
        )
        self.started = threading.Event()
        self.release_check = threading.Event()

        def callback():
            self.started.set()
            self.release_check.wait(3)

        self.app = ManagementApp(self.settings, self.state, callback)
        self.server = create_server("127.0.0.1", 0, self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.release_check.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(
        self,
        method,
        path,
        *,
        body="",
        cookie="",
        origin=True,
        host_header="",
        fetch_site="",
        origin_scheme="http",
        source_address=None,
    ):
        connection = http.client.HTTPConnection(
            self.host, self.port, timeout=2, source_address=source_address
        )
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if host_header:
            headers["Host"] = host_header
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = f"{origin_scheme}://{self.host}:{self.port}"
        if fetch_site:
            headers["Sec-Fetch-Site"] = fetch_site
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read().decode("utf-8")
        values = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, values, data

    def get_dashboard(self):
        status, headers, body = self.request("GET", "/")
        cookie = headers["set-cookie"].split(";", 1)[0]
        token = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        return status, headers, body, cookie, token

    def post(self, path, token, cookie, **fields):
        form = {"csrf": token, **fields}
        body = "&".join(f"{key}={value}" for key, value in form.items())
        return self.request("POST", path, body=body, cookie=cookie)


class ManagementWebTests(WebFixture):
    def test_server_rejects_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            create_server("192.0.2.10", 0, self.app)

    def test_container_wildcard_bind_is_explicitly_supported(self):
        server = create_server("0.0.0.0", 0, self.app)
        server.server_close()

    def test_dashboard_is_accessible_escaped_and_does_not_disclose_runtime_paths(self):
        self.state.update(lambda state: state.update({"enrollment_status": "<unsafe>", "vacancy_status": "available"}))
        status, headers, body, cookie, token = self.get_dashboard()
        self.assertEqual(status, 200)
        self.assertIn("SameSite=Strict", headers["set-cookie"])
        self.assertIn("&lt;unsafe&gt;", body)
        self.assertNotIn("hooks.example.invalid", body)
        self.assertNotIn("auth-state.json", body)
        self.assertNotIn("state.json", body)
        self.assertNotIn("<unsafe>", body)
        self.assertGreaterEqual(len(token), 32)
        health_status, _, health = self.request("GET", "/healthz")
        self.assertEqual((health_status, health), (200, '{"ok":true}'))

    def test_post_requires_same_origin_and_matching_csrf_cookie_and_form(self):
        _, _, _, cookie, token = self.get_dashboard()
        status, _, _ = self.request("POST", "/pause", body=f"csrf={token}", cookie=cookie, origin=False)
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST",
            "/pause",
            body=f"csrf={token}",
            cookie=cookie,
            origin=False,
            fetch_site="same-origin",
        )
        self.assertEqual(status, 303)
        self.state.set_monitoring_enabled(True)
        status, _, _ = self.post("/pause", "wrong-token", cookie)
        self.assertEqual(status, 403)
        self.assertTrue(self.state.read().get("monitoring_enabled", True))
        self.state.set_enrollment_armed(True)
        status, headers, _ = self.post("/pause", token, cookie)
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/?message=Monitoring%20paused.")
        self.assertFalse(self.state.read()["monitoring_enabled"])
        self.assertFalse(self.state.read()["enrollment_armed"])

    def test_dns_rebinding_host_is_rejected_for_get_and_post(self):
        status, _, _ = self.request("GET", "/", host_header="attacker.example")
        self.assertEqual(status, 403)
        _, _, _, cookie, token = self.get_dashboard()
        status, _, _ = self.request(
            "POST",
            "/pause",
            body=f"csrf={token}",
            cookie=cookie,
            host_header="attacker.example",
        )
        self.assertEqual(status, 403)

    def test_explicit_lan_host_is_allowed_but_unlisted_client_is_denied(self):
        self.app.settings = Settings(
            mode="enroll",
            enable_enrollment=True,
            acknowledgement=ENROLLMENT_ACK,
            data_path=self.settings.data_path,
            storage_state_path=self.settings.storage_state_path,
            ui_allowed_hosts=("localhost", "127.0.0.1", "::1", "192.168.1.199"),
            ui_allowed_clients=("127.0.0.1", "::1"),
        )
        status, _, _ = self.request("GET", "/", host_header="192.168.1.199:18782")
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "GET",
            "/",
            host_header="192.168.1.199:18782",
            source_address=("127.0.0.2", 0),
        )
        self.assertEqual(status, 403)

    def test_control_actions_and_exact_enrollment_arm_gate(self):
        _, _, _, cookie, token = self.get_dashboard()
        status, _, _ = self.post("/arm", token, cookie, ack="wrong")
        self.assertEqual(status, 403)
        self.assertFalse(self.state.read().get("enrollment_armed", False))
        status, _, _ = self.post("/arm", token, cookie, ack=ENROLLMENT_ACK)
        self.assertEqual(status, 303)
        self.assertTrue(self.state.read()["enrollment_armed"])
        status, _, _ = self.post("/disarm", token, cookie)
        self.assertEqual(status, 303)
        self.assertFalse(self.state.read()["enrollment_armed"])
        status, _, _ = self.post("/resume", token, cookie)
        self.assertEqual(status, 303)
        self.assertTrue(self.state.read()["monitoring_enabled"])

    def test_check_now_returns_before_slow_callback_finishes(self):
        _, _, _, cookie, token = self.get_dashboard()
        started = time.monotonic()
        status, _, _ = self.post("/check-now", token, cookie)
        elapsed = time.monotonic() - started
        self.assertEqual(status, 303)
        self.assertLess(elapsed, 0.5)
        self.assertTrue(self.started.wait(1))
        self.assertTrue(self.app.check_running)
        status, _, _ = self.post("/check-now", token, cookie)
        self.assertEqual(status, 409)
        self.release_check.set()
        for _ in range(20):
            if not self.app.check_running:
                break
            time.sleep(0.01)
        self.assertFalse(self.app.check_running)

    def test_arm_refuses_when_settings_gate_is_closed(self):
        self.app.settings = Settings(data_path=self.settings.data_path, storage_state_path=self.settings.storage_state_path)
        _, _, _, cookie, token = self.get_dashboard()
        status, _, _ = self.post("/arm", token, cookie, ack=ENROLLMENT_ACK)
        self.assertEqual(status, 403)
        self.assertFalse(self.state.read().get("enrollment_armed", False))

    def test_two_stage_authentication_requires_tls_and_never_echoes_credentials(self):
        _, _, _, cookie, token = self.get_dashboard()
        status, _, response = self.request("POST", "/auth/start", body=f"csrf={token}", cookie=cookie)
        self.assertEqual(status, 426)

        received = []
        flow = {"status": "idle", "captcha": None, "expires_in": 0}

        def start_authentication():
            flow.update(
                {
                    "status": "challenge",
                    "captcha": "data:image/png;base64,dGVzdA==",
                    "expires_in": 300,
                }
            )
            return True

        def submit_authentication(username, password, captcha):
            if flow["status"] != "challenge":
                return False
            received.append((username, password, captcha))
            flow.update({"status": "submitting", "captcha": None, "expires_in": 0})
            return True

        self.app.auth_start = start_authentication
        self.app.auth_snapshot = lambda: dict(flow)
        self.app.auth_submit = submit_authentication
        self.app.settings = Settings(
            mode="enroll",
            enable_enrollment=True,
            acknowledgement=ENROLLMENT_ACK,
            data_path=self.settings.data_path,
            storage_state_path=self.settings.storage_state_path,
            tls_enabled=True,
        )
        status, _, response = self.request(
            "POST",
            "/auth/start",
            body=f"csrf={token}",
            cookie=cookie,
            origin_scheme="https",
        )
        self.assertEqual(status, 303)
        status, headers, challenge_page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('src="data:image/png;base64,dGVzdA=="', challenge_page)
        self.assertIn('action="/auth/submit"', challenge_page)
        submit_cookie = headers.get("set-cookie", cookie).split(";", 1)[0]
        submit_token = re.search(r'name="csrf" value="([^"]+)"', challenge_page).group(1)
        body = (
            f"csrf={submit_token}&username=test-user&password=test-password&captcha=human-answer"
        )
        status, _, response = self.request(
            "POST",
            "/auth/submit",
            body=body,
            cookie=submit_cookie,
            origin_scheme="https",
        )
        self.assertEqual(status, 303)
        self.assertNotIn("test-user", response)
        self.assertNotIn("test-password", response)
        self.assertNotIn("human-answer", response)
        self.assertEqual(received, [("test-user", "test-password", "human-answer")])
        status, _, _ = self.request(
            "POST",
            "/auth/submit",
            body=body,
            cookie=submit_cookie,
            origin_scheme="https",
        )
        self.assertEqual(status, 409)

    def test_ready_login_without_portal_captcha_does_not_require_a_response(self):
        received = []
        self.app.auth_snapshot = lambda: {
            "status": "challenge",
            "captcha": None,
            "expires_in": 300,
        }
        self.app.auth_submit = lambda username, password, captcha: (
            received.append((username, password, captcha)) or True
        )
        self.app.settings = Settings(
            data_path=self.settings.data_path,
            storage_state_path=self.settings.storage_state_path,
            tls_enabled=True,
        )

        _, _, body, cookie, token = self.get_dashboard()
        self.assertIn('action="/auth/submit"', body)
        self.assertIn('name="username"', body)
        self.assertIn('name="password"', body)
        self.assertNotIn('name="captcha"', body)
        self.assertNotIn("University CAPTCHA challenge", body)
        status, _, response = self.request(
            "POST",
            "/auth/submit",
            body=f"csrf={token}&username=test-user&password=test-password",
            cookie=cookie,
            origin_scheme="https",
        )
        self.assertEqual(status, 303)
        self.assertNotIn("test-user", response)
        self.assertNotIn("test-password", response)
        self.assertEqual(received, [("test-user", "test-password", "")])

    def test_check_and_arm_refuse_while_monitoring_is_paused(self):
        self.state.set_monitoring_enabled(False)
        _, _, _, cookie, token = self.get_dashboard()
        status, _, _ = self.post("/check-now", token, cookie)
        self.assertEqual(status, 409)
        status, _, _ = self.post("/arm", token, cookie, ack=ENROLLMENT_ACK)
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
