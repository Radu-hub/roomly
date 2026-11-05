"""The app must refuse to start as a role that bypasses Row-Level Security."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from roomly import db

from .conftest import APP_URL, OWNER_URL

APP_DIR = Path(__file__).resolve().parents[1]


def test_check_is_false_for_owner_and_true_for_app():
    assert db.rls_startup_check(OWNER_URL) is False
    assert db.rls_startup_check(APP_URL) is True


def test_check_is_none_when_database_is_unreachable():
    assert db.rls_startup_check("host=127.0.0.1 port=1 dbname=roomly user=x") is None


def test_process_exits_nonzero_when_connected_as_owner():
    env = os.environ | {
        "DATABASE_URL": OWNER_URL,
        "SESSION_SECRET": "test-only-session-secret",
        "APP_ENV": "local",
    }
    result = subprocess.run(
        [sys.executable, "-m", "roomly.main"],
        cwd=APP_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "refusing to start" in result.stdout
    assert '"level": "CRITICAL"' in result.stdout


def test_real_server_workers_write_json_request_logs_to_stdout():
    """Runs the real multi-worker server. Each worker is a new process, so a logging setup
    done only in the parent would silently produce no request logs at all."""
    port = "18765"
    env = os.environ | {
        "DATABASE_URL": APP_URL,
        "SESSION_SECRET": "test-only-session-secret",
        "APP_ENV": "local",
        "PORT": port,
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "roomly.main"],
        cwd=APP_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(100):
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{port}/health",
                    headers={"X-Request-ID": "log-test-1"},
                    timeout=2,
                )
                break
            except httpx.TransportError:
                time.sleep(0.2)
        assert response.status_code == 200
    finally:
        server.terminate()
        output, _ = server.communicate(timeout=20)

    entries = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    matching = [e for e in entries if e.get("request_id") == "log-test-1"]
    assert len(matching) == 1
    entry = matching[0]
    assert entry["msg"] == "request"
    assert entry["method"] == "GET"
    assert entry["path"] == "/health"
    assert entry["status"] == 200
    assert entry["instance_id"] == "local"
    assert {"ts", "level", "duration_ms", "org", "user_id"} <= set(entry)
