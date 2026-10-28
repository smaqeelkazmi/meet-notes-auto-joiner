from __future__ import annotations

from pathlib import Path

import yaml

from meet_notes.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
BANNED = (
    "aria-label=",
    "Take notes",
    "Leave call",
    "Join now",
    "Turn off microphone",
    "Turn off camera",
    "Start taking notes",
)


def test_selectors_live_in_one_module():
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.name == "selectors.py":
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in BANNED:
            if phrase in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {phrase!r}")
    assert offenders == []


def test_deploy_files():
    service = (ROOT / "deploy" / "meet-notes.service").read_text(encoding="utf-8")
    assert "ExecStart=" in service
    assert "Restart=" in service
    docker_service = (ROOT / "deploy" / "meet-notes-docker.service").read_text(encoding="utf-8")
    assert "docker compose up" in docker_service
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "mcr.microsoft.com/playwright/python:v1.64.1-noble" in dockerfile
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "meet-notes" in compose["services"]
    assert compose["services"]["meet-notes"]["shm_size"] == "2gb"
    plist = (ROOT / "deploy" / "com.rev9.meet-notes.plist").read_text(encoding="utf-8")
    assert "com.rev9.meet-notes" in plist
    assert "SuccessfulExit" in plist
    assert "ThrottleInterval" in plist
    assert "launchd.out" in plist and "launchd.err" in plist


def test_parser_commands():
    parser = build_parser()
    args = parser.parse_args(["status", "--json", "--strict"])
    assert args.command == "status" and args.json and args.strict
    run = parser.parse_args(["run", "--dry-run"])
    assert run.command == "run" and run.dry_run
    assert parser.parse_args(["version"]).command == "version"
    assert parser.parse_args(["test-email"]).command == "test-email"
