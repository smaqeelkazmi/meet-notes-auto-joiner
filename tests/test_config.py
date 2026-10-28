from __future__ import annotations

from pathlib import Path

import pytest

from meet_notes.config import load_config
from meet_notes.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads():
    cfg = load_config(ROOT / "config.example.yaml", environ={}, cwd=ROOT)
    assert cfg.calendar_id == "primary"
    assert cfg.timezone == "Asia/Karachi"
    assert cfg.stay_mode == "leave_after_notes"
    assert cfg.max_concurrent_joins == 4
    assert cfg.join_lead_minutes == 3
    assert cfg.skip_declined is False
    assert cfg.wait_for_participant is False
    assert cfg.only_organizer_meetings is True
    assert cfg.notify_enabled is False
    assert cfg.notes_ui_timeout_seconds == 45
    assert cfg.meeting_log == cfg.state_db.parent / "meeting-log.csv"
    assert cfg.self_display_names == ("You", "Aqeel")
    assert cfg.push_enabled is False
    assert cfg.profile_dir is not None
    assert cfg.credentials_file is not None


def test_docker_example_uses_container_paths():
    cfg = load_config(ROOT / "config.docker.example.yaml", environ={}, cwd=ROOT)
    assert cfg.profile_dir == Path("/profile")
    assert cfg.token_file == Path("/data/token.json")
    assert cfg.credentials_file == Path("/secrets/credentials.json")
    assert cfg.no_sandbox is True
    assert cfg.max_concurrent_joins == 4


def test_env_overrides_yaml(tmp_path: Path):
    cfg = load_config(
        ROOT / "config.example.yaml",
        environ={
            "MEET_NOTES_JOIN_LEAD_MINUTES": "9",
            "MEET_NOTES_STAY_MODE": "until_alone",
            "MEET_NOTES_SKIP_DECLINED": "true",
            "MEET_NOTES_PROFILE_DIR": "profiles/browser",
            "MEET_NOTES_SELF_DISPLAY_NAMES": "You, Aqeel Syed",
            "MEET_NOTES_MAX_CONCURRENT_JOINS": "2",
        },
        cwd=tmp_path,
    )
    assert cfg.join_lead_minutes == 9
    assert cfg.stay_mode == "until_alone"
    assert cfg.skip_declined is True
    assert cfg.profile_dir == tmp_path / "profiles" / "browser"
    assert cfg.self_display_names == ("You", "Aqeel Syed")
    assert cfg.max_concurrent_joins == 2


def test_notes_confirm_grace_is_at_most_60_seconds(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("notes_ui_timeout_seconds: 90\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path, environ={}, cwd=tmp_path)


def test_concurrent_joins_must_be_from_1_to_8(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("max_concurrent_joins: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path, environ={}, cwd=tmp_path)
    path.write_text("max_concurrent_joins: 9\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path, environ={}, cwd=tmp_path)


def test_unknown_key_and_bad_stay_mode(tmp_path: Path):
    bad = tmp_path / "config.yaml"
    bad.write_text("stay_mode: camp-forever\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad, environ={}, cwd=tmp_path)
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("calendar_id: primary\nnot_a_real_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(unknown, environ={}, cwd=tmp_path)


def test_push_requires_https_and_token(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "push:\n  enabled: true\n  public_url: http://example.com/hook\n  channel_token: secret\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path, environ={}, cwd=tmp_path)
    path.write_text(
        "push:\n  enabled: true\n  public_url: https://example.com/hook\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path, environ={}, cwd=tmp_path)


def test_missing_config_file(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml", environ={}, cwd=tmp_path)
