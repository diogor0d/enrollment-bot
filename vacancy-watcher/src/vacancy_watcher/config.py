"""Configuration for the independent InforEstudante watcher."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse


LIST_URL = "https://inforestudante.uc.pt/nonio/inscturmas/listaInscricoes.do"
EXPECTED_HOST = "inforestudante.uc.pt"
EXPECTED_LIST_PATH = "/nonio/inscturmas/listaInscricoes.do"
COURSE_CODE = "02038756"
COURSE_TITLE = "Segurança e Privacidade"
CLASS_NAME = "PL3"
EXPECTED_CLASS_ID = "249089"
PROFILE_ALT = "PL"
ENROLLMENT_ACK = f"{COURSE_CODE}:{CLASS_NAME}:{EXPECTED_CLASS_ID}"
UI_HOST = "0.0.0.0"
UI_PORT = 8080
LOOPBACK_UI_HOSTS = ("localhost", "127.0.0.1", "::1")
LOOPBACK_UI_CLIENTS = ("127.0.0.1", "::1")
TLS_CERT_PATH = Path("data/tls.crt")
TLS_KEY_PATH = Path("data/tls.key")


class ConfigError(ValueError):
    """Raised when an environment configuration cannot be used safely."""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be numeric") from exc
    if value < 0:
        raise ConfigError(f"{name} must not be negative")
    return value


def _env_ip_allowlist(name: str, defaults: tuple[str, ...], *, allow_localhost: bool = False) -> tuple[str, ...]:
    """Parse a comma-separated literal-IP allowlist without trusting DNS."""

    values = list(defaults)
    for raw_value in os.getenv(name, "").split(","):
        value = raw_value.strip().lower()
        if not value:
            continue
        if allow_localhost and value == "localhost":
            values.append(value)
            continue
        try:
            values.append(str(ipaddress.ip_address(value)))
        except ValueError as exc:
            raise ConfigError(f"{name} must contain only literal IP addresses") from exc
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class Settings:
    """Runtime settings with portal safety constants kept non-configurable."""

    list_url: str = LIST_URL
    expected_host: str = EXPECTED_HOST
    expected_list_path: str = EXPECTED_LIST_PATH
    course_code: str = COURSE_CODE
    course_title: str = COURSE_TITLE
    class_name: str = CLASS_NAME
    expected_class_id: str = EXPECTED_CLASS_ID
    profile_alt: str = PROFILE_ALT
    mode: str = "notify"
    enable_enrollment: bool = False
    acknowledgement: str = ""
    storage_state_path: Path = Path("data/auth-state.json")
    data_path: Path = Path("data/state.json")
    webhook_url: str | None = None
    poll_interval: float = 30.0
    jitter: float = 2.0
    auth_retry_interval: float = 900.0
    stop_after_success: bool = True
    ui_host: str = UI_HOST
    ui_port: int = UI_PORT
    ui_allowed_hosts: tuple[str, ...] = LOOPBACK_UI_HOSTS
    ui_allowed_clients: tuple[str, ...] = LOOPBACK_UI_CLIENTS
    tls_enabled: bool = False
    tls_cert_path: Path = TLS_CERT_PATH
    tls_key_path: Path = TLS_KEY_PATH

    @property
    def enrollment_gate(self) -> bool:
        """Whether all three independent enrollment gates are open."""

        return (
            self.mode == "enroll"
            and self.enable_enrollment
            and self.acknowledgement == ENROLLMENT_ACK
        )

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("MODE", "notify").lower()
        if mode not in {"notify", "enroll"}:
            raise ConfigError("MODE must be notify or enroll")
        interval = max(10.0, _env_float("POLL_INTERVAL", 30.0))
        jitter = _env_float("POLL_JITTER", 2.0)
        return cls(
            mode=mode,
            enable_enrollment=_env_bool("ENABLE_ENROLLMENT", False),
            acknowledgement=os.getenv("ENROLLMENT_ACK", ""),
            storage_state_path=Path(os.getenv("STORAGE_STATE_PATH", "data/auth-state.json")),
            data_path=Path(os.getenv("DATA_PATH", "data/state.json")),
            webhook_url=os.getenv("WEBHOOK_URL") or None,
            poll_interval=interval,
            jitter=jitter,
            auth_retry_interval=max(60.0, _env_float("AUTH_RETRY_INTERVAL", 900.0)),
            stop_after_success=_env_bool("STOP_AFTER_SUCCESS", True),
            ui_allowed_hosts=_env_ip_allowlist(
                "UI_ALLOWED_HOSTS", LOOPBACK_UI_HOSTS, allow_localhost=True
            ),
            ui_allowed_clients=_env_ip_allowlist("UI_ALLOWED_CLIENTS", LOOPBACK_UI_CLIENTS),
            tls_enabled=_env_bool("TLS_ENABLED", False),
            tls_cert_path=Path(os.getenv("TLS_CERT_PATH", str(TLS_CERT_PATH))),
            tls_key_path=Path(os.getenv("TLS_KEY_PATH", str(TLS_KEY_PATH))),
        )

    def validate(self) -> None:
        if self.list_url != LIST_URL:
            raise ConfigError("LIST_URL is fixed to the verified HTTPS portal URL")
        if self.expected_host != EXPECTED_HOST or self.expected_list_path != EXPECTED_LIST_PATH:
            raise ConfigError("portal host/path assertions are fixed")
        if (
            self.course_code != COURSE_CODE
            or self.course_title != COURSE_TITLE
            or self.class_name != CLASS_NAME
            or self.expected_class_id != EXPECTED_CLASS_ID
            or self.profile_alt != PROFILE_ALT
        ):
            raise ConfigError("course, class, and profile assertions are fixed")
        if self.mode not in {"notify", "enroll"}:
            raise ConfigError("mode must be notify or enroll")
        if self.poll_interval < 10:
            raise ConfigError("poll interval must be at least 10 seconds")
        if self.jitter < 0:
            raise ConfigError("poll jitter must not be negative")
        if self.auth_retry_interval < 60:
            raise ConfigError("authentication retry interval must be at least 60 seconds")
        if self.ui_host != UI_HOST or self.ui_port != UI_PORT:
            raise ConfigError("management UI host and port are fixed for the container boundary")
        for host in self.ui_allowed_hosts:
            if host == "localhost":
                continue
            try:
                ipaddress.ip_address(host)
            except ValueError as exc:
                raise ConfigError("management UI allowed hosts must be literal IP addresses") from exc
        for client in self.ui_allowed_clients:
            try:
                ipaddress.ip_address(client)
            except ValueError as exc:
                raise ConfigError("management UI allowed clients must be literal IP addresses") from exc
        if self.tls_enabled:
            if not self.tls_cert_path.is_file() or not self.tls_key_path.is_file():
                raise ConfigError("TLS certificate and key files are required when TLS is enabled")
        if self.webhook_url:
            webhook = urlparse(self.webhook_url)
            try:
                port = webhook.port
            except ValueError as exc:
                raise ConfigError("WEBHOOK_URL has an invalid port") from exc
            if (
                webhook.scheme != "https"
                or not webhook.hostname
                or port not in {None, 443}
                or webhook.username is not None
                or webhook.password is not None
                or webhook.fragment
            ):
                raise ConfigError(
                    "WEBHOOK_URL must use HTTPS on the default port without credentials or a fragment"
                )
