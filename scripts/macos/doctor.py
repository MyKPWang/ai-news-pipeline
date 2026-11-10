#!/usr/bin/env python3
"""Non-invasive readiness checks for the local macOS deployment."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = ROOT / "deploy" / "macos" / "local-machine.env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


class Doctor:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"OK    {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"WARN  {message}")

    def error(self, message: str) -> None:
        self.errors.append(message)
        print(f"ERROR {message}")


def executable_check(doctor: Doctor, label: str, path: Path) -> None:
    if path.is_file() and os.access(path, os.X_OK):
        doctor.ok(f"{label}: {path}")
    else:
        doctor.error(f"{label} is missing or not executable: {path}")


def http_ready(doctor: Doctor, url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            if 200 <= response.status < 300:
                doctor.ok(f"collector readiness endpoint: {url}")
                return
            doctor.warn(f"collector readiness returned HTTP {response.status}: {url}")
    except OSError:
        doctor.error(f"collector API is not ready at {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local AI News deployment readiness")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--strict", action="store_true", help="Return non-zero on any warning")
    args = parser.parse_args()

    doctor = Doctor()
    env = load_env(args.env)
    if args.env.exists():
        doctor.ok(f"local environment file: {args.env}")
        mode = args.env.stat().st_mode & 0o777
        if mode & 0o077:
            doctor.warn(f"local environment file should be owner-only (current mode {mode:03o})")
    else:
        doctor.error(f"local environment file is missing: {args.env}")
        return 2

    pipeline_home = Path(env.get("PIPELINE_HOME", str(ROOT)))
    collector_home = Path(env.get("COLLECTOR_HOME", ""))
    collector_config = Path(env.get("COLLECTOR_CONFIG", ""))
    token = env.get("PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN", "") or env.get("WECHAT_COLLECTOR_PIPELINE_TOKEN", "")
    host = env.get("COLLECTOR_API_HOST", "127.0.0.1")
    port = int(env.get("COLLECTOR_API_PORT", "4100"))

    executable_check(doctor, "Pipeline Python", pipeline_home / ".venv" / "bin" / "python")
    collector_cli = collector_home / ".venv" / "bin" / "public-source-collector"
    if not collector_cli.is_file():
        collector_cli = collector_home / ".venv" / "bin" / "wechat-list-collector"
    executable_check(doctor, "Collector CLI", collector_cli)
    for label, path in (
        ("Pipeline config", pipeline_home / "config.yaml"),
        ("Pipeline secrets", pipeline_home / "secrets.yaml"),
        ("Collector config", collector_config),
        ("Public-source catalog", collector_home / "public-sources.toml"),
    ):
        (doctor.ok if path.is_file() else doctor.error)(f"{label}: {path}")

    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome.is_file():
        doctor.ok("Google Chrome is available for browser-backed public sources")
    else:
        doctor.error("Google Chrome is missing; browser-backed sources cannot run")

    if token and token.lower() not in {"change-me", "replace-me"}:
        doctor.ok("collector API token is configured")
    else:
        doctor.error("PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN is missing in local-machine.env")

    try:
        with socket.create_connection((host, port), timeout=2):
            doctor.ok(f"collector TCP listener: {host}:{port}")
    except OSError:
        doctor.error(f"collector TCP listener is unavailable: {host}:{port}")
    http_ready(doctor, f"http://{host}:{port}/health/ready")

    label = "com.wangkp.ai-news.collector-api"
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        doctor.ok(f"launchd API job loaded: {label}")
    else:
        doctor.warn(f"launchd API job is not loaded: {label}")

    if doctor.errors or (args.strict and doctor.warnings):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
