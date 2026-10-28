"""Config file and environment. Environment variables win over the YAML file.

Paths in the YAML file are relative to the file. Paths in the environment
are relative to the current directory. Blank path fields use the per-user
config directory (~/.config/meet-notes, or %APPDATA%\\meet-notes on Windows).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from meet_notes.errors import ConfigError

STAY_MODES = frozenset({"leave_after_notes", "until_meeting_end", "until_alone"})
BROWSER_CHANNELS = frozenset({"chromium", "chrome", "msedge"})
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_PATH_FIELDS = (
    "profile_dir",
    "credentials_file",
    "token_file",
    "state_db",
    "log_dir",
    "meeting_log",
)

_ENV_FIELDS: dict[str, tuple[str, str]] = {
    "MEET_NOTES_CALENDAR_ID": ("calendar_id", "str"),
    "MEET_NOTES_TIMEZONE": ("timezone", "str"),
    "MEET_NOTES_POLL_INTERVAL_SECONDS": ("poll_interval_seconds", "int"),
    "MEET_NOTES_LOOKAHEAD_HOURS": ("lookahead_hours", "int"),
    "MEET_NOTES_JOIN_LEAD_MINUTES": ("join_lead_minutes", "int"),
    "MEET_NOTES_JOIN_AFTER_START_MINUTES": ("join_after_start_minutes", "int"),
    "MEET_NOTES_NOBODY_JOINED_TIMEOUT_MINUTES": ("nobody_joined_timeout_minutes", "int"),
    "MEET_NOTES_ADMIT_TIMEOUT_MINUTES": ("admit_timeout_minutes", "int"),
    "MEET_NOTES_PARTICIPANT_POLL_SECONDS": ("participant_poll_seconds", "float"),
    "MEET_NOTES_ALONE_CONFIRM_SECONDS": ("alone_confirm_seconds", "float"),
    "MEET_NOTES_STAY_GRACE_MINUTES": ("stay_grace_minutes", "int"),
    "MEET_NOTES_NOTES_UI_TIMEOUT_SECONDS": ("notes_ui_timeout_seconds", "float"),
    "MEET_NOTES_NAVIGATION_TIMEOUT_SECONDS": ("navigation_timeout_seconds", "int"),
    "MEET_NOTES_STAY_MODE": ("stay_mode", "str"),
    "MEET_NOTES_WAIT_FOR_PARTICIPANT": ("wait_for_participant", "bool"),
    "MEET_NOTES_ONLY_ORGANIZER_MEETINGS": ("only_organizer_meetings", "bool"),
    "MEET_NOTES_LEAVE_EVEN_IF_UNVERIFIED": ("leave_even_if_unverified", "bool"),
    "MEET_NOTES_STAY_EVEN_IF_UNVERIFIED": ("stay_even_if_unverified", "bool"),
    "MEET_NOTES_SKIP_DECLINED": ("skip_declined", "bool"),
    "MEET_NOTES_SKIP_ALL_DAY": ("skip_all_day", "bool"),
    "MEET_NOTES_MAX_PREJOIN_ATTEMPTS": ("max_prejoin_attempts", "int"),
    "MEET_NOTES_MAX_CONCURRENT_JOINS": ("max_concurrent_joins", "int"),
    "MEET_NOTES_MISSING_POLLS_BEFORE_CANCEL": ("missing_polls_before_cancel", "int"),
    "MEET_NOTES_SELF_DISPLAY_NAMES": ("self_display_names", "list"),
    "MEET_NOTES_HEADLESS": ("headless", "bool"),
    "MEET_NOTES_BROWSER_LOCALE": ("browser_locale", "str"),
    "MEET_NOTES_BROWSER_CHANNEL": ("browser_channel", "str"),
    "MEET_NOTES_NO_SANDBOX": ("no_sandbox", "bool"),
    "MEET_NOTES_SCREENSHOT_ON_FAILURE": ("screenshot_on_failure", "bool"),
    "MEET_NOTES_PROFILE_DIR": ("profile_dir", "path"),
    "MEET_NOTES_CREDENTIALS_FILE": ("credentials_file", "path"),
    "MEET_NOTES_TOKEN_FILE": ("token_file", "path"),
    "MEET_NOTES_STATE_DB": ("state_db", "path"),
    "MEET_NOTES_LOG_DIR": ("log_dir", "path"),
    "MEET_NOTES_MEETING_LOG": ("meeting_log", "path"),
    "MEET_NOTES_LOG_LEVEL": ("log_level", "str"),
    "MEET_NOTES_DRY_RUN": ("dry_run", "bool"),
    "MEET_NOTES_OAUTH_PORT": ("oauth_port", "int"),
    "MEET_NOTES_PUSH_ENABLED": ("push_enabled", "bool"),
    "MEET_NOTES_PUSH_LISTEN_HOST": ("push_listen_host", "str"),
    "MEET_NOTES_PUSH_LISTEN_PORT": ("push_listen_port", "int"),
    "MEET_NOTES_PUSH_PUBLIC_URL": ("push_public_url", "str"),
    "MEET_NOTES_PUSH_CHANNEL_TOKEN": ("push_channel_token", "str"),
    "MEET_NOTES_PUSH_RENEW_MINUTES_BEFORE": ("push_renew_minutes_before", "int"),
    "MEET_NOTES_NOTIFY_ENABLED": ("notify_enabled", "bool"),
    "MEET_NOTES_NOTIFY_TO": ("notify_to", "str"),
    "MEET_NOTES_NOTIFY_FROM": ("notify_from", "str"),
    "MEET_NOTES_NOTIFY_SMTP_HOST": ("notify_smtp_host", "str"),
    "MEET_NOTES_NOTIFY_SMTP_PORT": ("notify_smtp_port", "int"),
    "MEET_NOTES_NOTIFY_SMTP_SSL": ("notify_smtp_ssl", "bool"),
    "MEET_NOTES_NOTIFY_SMTP_USERNAME": ("notify_smtp_username", "str"),
}

_PUSH_KEYS = {
    "enabled": "push_enabled",
    "listen_host": "push_listen_host",
    "listen_port": "push_listen_port",
    "public_url": "push_public_url",
    "channel_token": "push_channel_token",
    "renew_minutes_before": "push_renew_minutes_before",
}

_NOTIFY_KEYS = {
    "enabled": "notify_enabled",
    "to": "notify_to",
    "from": "notify_from",
    "smtp_host": "notify_smtp_host",
    "smtp_port": "notify_smtp_port",
    "smtp_ssl": "notify_smtp_ssl",
    "smtp_username": "notify_smtp_username",
}


def default_config_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("APPDATA")
        if root:
            return Path(root) / "meet-notes"
    return Path.home() / ".config" / "meet-notes"


@dataclass
class AppConfig:
    calendar_id: str = "primary"
    timezone: str = "Asia/Karachi"
    poll_interval_seconds: int = 60
    lookahead_hours: int = 48
    join_lead_minutes: int = 3
    join_after_start_minutes: int = 10
    nobody_joined_timeout_minutes: int = 10
    admit_timeout_minutes: int = 5
    participant_poll_seconds: float = 5
    alone_confirm_seconds: float = 20
    stay_grace_minutes: int = 2
    notes_ui_timeout_seconds: float = 45
    navigation_timeout_seconds: int = 45
    stay_mode: str = "leave_after_notes"
    wait_for_participant: bool = False
    only_organizer_meetings: bool = True
    leave_even_if_unverified: bool = True
    stay_even_if_unverified: bool = False
    skip_declined: bool = False
    skip_all_day: bool = True
    max_prejoin_attempts: int = 3
    max_concurrent_joins: int = 4
    missing_polls_before_cancel: int = 2
    self_display_names: tuple[str, ...] = ("You", "Aqeel")
    headless: bool = True
    browser_locale: str = "en-US"
    browser_channel: str = "chromium"
    no_sandbox: bool = False
    screenshot_on_failure: bool = True
    profile_dir: Path | None = None
    credentials_file: Path | None = None
    token_file: Path | None = None
    state_db: Path | None = None
    log_dir: Path | None = None
    meeting_log: Path | None = None
    log_level: str = "INFO"
    dry_run: bool = False
    oauth_port: int = 0
    push_enabled: bool = False
    push_listen_host: str = "127.0.0.1"
    push_listen_port: int = 8766
    push_public_url: str = ""
    push_channel_token: str = ""
    push_renew_minutes_before: int = 60
    notify_enabled: bool = False
    notify_to: str = ""
    notify_from: str = ""
    notify_smtp_host: str = ""
    notify_smtp_port: int = 465
    notify_smtp_ssl: bool = True
    notify_smtp_username: str = ""
    config_path: Path | None = None

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def validate(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown timezone: {self.timezone}") from exc
        if self.stay_mode not in STAY_MODES:
            allowed = ", ".join(sorted(STAY_MODES))
            raise ConfigError(f"stay_mode must be one of: {allowed}")
        if self.browser_channel not in BROWSER_CHANNELS:
            raise ConfigError("browser_channel must be chromium, chrome, or msedge")
        if self.poll_interval_seconds < 1:
            raise ConfigError("poll_interval_seconds must be at least 1")
        if self.lookahead_hours < 1:
            raise ConfigError("lookahead_hours must be at least 1")
        if self.join_lead_minutes < 0:
            raise ConfigError("join_lead_minutes cannot be negative")
        if self.join_after_start_minutes < 0:
            raise ConfigError("join_after_start_minutes cannot be negative")
        for name in (
            "nobody_joined_timeout_minutes",
            "admit_timeout_minutes",
            "max_prejoin_attempts",
            "missing_polls_before_cancel",
            "navigation_timeout_seconds",
        ):
            if int(getattr(self, name)) < 1:
                raise ConfigError(f"{name} must be at least 1")
        if self.participant_poll_seconds <= 0:
            raise ConfigError("participant_poll_seconds must be positive")
        if self.alone_confirm_seconds < 0:
            raise ConfigError("alone_confirm_seconds cannot be negative")
        if self.notes_ui_timeout_seconds <= 0 or self.notes_ui_timeout_seconds > 60:
            raise ConfigError("notes_ui_timeout_seconds must be greater than 0 and at most 60")
        if self.notify_enabled:
            if not self.notify_to or "@" not in self.notify_to:
                raise ConfigError("notify.to must be an email address when notify is enabled")
            if not self.notify_from or "@" not in self.notify_from:
                raise ConfigError("notify.from must be an email address when notify is enabled")
            if not self.notify_smtp_host:
                raise ConfigError("notify.smtp_host is required when notify is enabled")
            if self.notify_smtp_port < 1 or self.notify_smtp_port > 65535:
                raise ConfigError("notify.smtp_port is not a valid TCP port")
        if self.max_concurrent_joins < 1 or self.max_concurrent_joins > 8:
            raise ConfigError("max_concurrent_joins must be from 1 to 8")
        if not self.self_display_names:
            raise ConfigError("self_display_names needs at least one name")
        level = self.log_level.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigError("log_level must be DEBUG, INFO, WARNING, or ERROR")
        self.log_level = level
        if self.push_enabled:
            if not self.push_public_url.startswith("https://"):
                raise ConfigError("push.public_url must be an https URL when push is enabled")
            if not self.push_channel_token:
                raise ConfigError(
                    "push.channel_token is required when push is enabled "
                    "(set push.channel_token or MEET_NOTES_PUSH_CHANNEL_TOKEN)"
                )
            if self.push_listen_port < 1 or self.push_listen_port > 65535:
                raise ConfigError("push.listen_port is not a valid TCP port")
        if self.oauth_port < 0 or self.oauth_port > 65535:
            raise ConfigError("oauth_port is not a valid TCP port")


def discover_config_path(environ: Mapping[str, str], cwd: Path) -> Path | None:
    raw = environ.get("MEET_NOTES_CONFIG")
    if raw:
        return Path(raw).expanduser()
    local = cwd / "config.yaml"
    if local.exists():
        return local
    default = default_config_dir() / "config.yaml"
    if default.exists():
        return default
    return None


def load_config(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> AppConfig:
    env = os.environ if environ is None else environ
    workdir = Path.cwd() if cwd is None else cwd
    if path is None and environ is None and cwd is None:
        path = discover_config_path(os.environ, Path.cwd())
    elif path is None and (environ is not None or cwd is not None):
        path = discover_config_path(env, workdir)

    data: dict[str, Any] = {}
    base = workdir
    config_path: Path | None = None
    if path is not None:
        config_path = Path(path).expanduser()
        if not config_path.is_absolute():
            config_path = workdir / config_path
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError("Config file must be a YAML mapping")
        data = dict(loaded)
        base = config_path.parent

    _flatten_push(data)
    _flatten_notify(data)
    _reject_unknown(data)
    cfg = AppConfig(config_path=config_path)
    _apply_mapping(cfg, data, base)
    _apply_env(cfg, env, workdir)
    _fill_default_paths(cfg)
    cfg.validate()
    return cfg


def ensure_runtime_dirs(cfg: AppConfig) -> None:
    for path in (
        cfg.profile_dir,
        cfg.log_dir,
        cfg.state_db.parent if cfg.state_db else None,
        cfg.token_file.parent if cfg.token_file else None,
        cfg.credentials_file.parent if cfg.credentials_file else None,
        cfg.meeting_log.parent if cfg.meeting_log else None,
    ):
        if path is None:
            continue
        path.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass


def _flatten_push(data: dict[str, Any]) -> None:
    if "push" not in data:
        return
    push = data.pop("push") or {}
    if not isinstance(push, dict):
        raise ConfigError("push must be a mapping")
    unknown = sorted(set(push) - set(_PUSH_KEYS))
    if unknown:
        raise ConfigError(f"Unknown push config keys: {', '.join(unknown)}")
    for key, target in _PUSH_KEYS.items():
        if key in push:
            data[target] = push[key]


def _flatten_notify(data: dict[str, Any]) -> None:
    if "notify" not in data:
        return
    notify = data.pop("notify") or {}
    if not isinstance(notify, dict):
        raise ConfigError("notify must be a mapping")
    unknown = sorted(set(notify) - set(_NOTIFY_KEYS))
    if unknown:
        raise ConfigError(
            "Unknown notify config keys: "
            + ", ".join(unknown)
            + ". The SMTP password is not a config setting. "
            "Set MEET_NOTES_SMTP_PASSWORD or create a mode-600 smtp_password file."
        )
    for key, target in _NOTIFY_KEYS.items():
        if key in notify:
            data[target] = notify[key]


def _reject_unknown(data: dict[str, Any]) -> None:
    known = {item.name for item in fields(AppConfig)} - {"config_path"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"Unknown config keys: {', '.join(unknown)}")


def _apply_mapping(cfg: AppConfig, data: dict[str, Any], base: Path) -> None:
    for key, value in data.items():
        setattr(cfg, key, _coerce_field(key, value, base))


def _apply_env(cfg: AppConfig, env: Mapping[str, str], cwd: Path) -> None:
    for env_name, (field_name, kind) in _ENV_FIELDS.items():
        if env_name not in env:
            continue
        setattr(cfg, field_name, _coerce_kind(field_name, kind, env[env_name], cwd))


def _coerce_field(field_name: str, value: Any, base: Path) -> Any:
    if field_name in _PATH_FIELDS:
        return _coerce_path(value, base)
    if field_name == "self_display_names":
        if isinstance(value, str):
            return _parse_list(value)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError("self_display_names must be a list of strings")
        names = tuple(item.strip() for item in value if item.strip())
        return names
    expected = _field_kind(field_name)
    if expected == "bool" and not isinstance(value, bool):
        raise ConfigError(f"{field_name} must be true or false")
    if expected == "int" and not isinstance(value, int):
        raise ConfigError(f"{field_name} must be an integer")
    if expected == "float" and not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} must be a number")
    if expected == "float":
        return float(value)
    if expected == "str" and not isinstance(value, str):
        raise ConfigError(f"{field_name} must be a string")
    return value


def _field_kind(field_name: str) -> str:
    for _, (name, kind) in _ENV_FIELDS.items():
        if name == field_name:
            return kind
    return "str"


def _coerce_kind(field_name: str, kind: str, raw: str, base: Path) -> Any:
    if kind == "bool":
        return _parse_bool(field_name, raw)
    if kind == "int":
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{field_name} must be an integer") from exc
    if kind == "float":
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"{field_name} must be a number") from exc
    if kind == "list":
        return _parse_list(raw)
    if kind == "path":
        return _coerce_path(raw, base)
    return raw


def _parse_bool(field_name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{field_name} must be true or false")


def _parse_list(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _coerce_path(value: Any, base: Path) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def _fill_default_paths(cfg: AppConfig) -> None:
    root = default_config_dir()
    if cfg.profile_dir is None:
        cfg.profile_dir = root / "browser-profile"
    if cfg.credentials_file is None:
        cfg.credentials_file = root / "credentials.json"
    if cfg.token_file is None:
        cfg.token_file = root / "token.json"
    if cfg.state_db is None:
        cfg.state_db = root / "state.db"
    if cfg.log_dir is None:
        cfg.log_dir = root / "logs"
    if cfg.meeting_log is None and cfg.state_db is not None:
        cfg.meeting_log = cfg.state_db.parent / "meeting-log.csv"
