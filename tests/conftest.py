"""Shared test fixtures for VibeNode.

SAFETY:
- Tests spin up a SEPARATE server on port 5099 with its own sqlite DB.
- The user's running instance on :5050 is NEVER touched.
- The user's kanban_config.json is NEVER touched.
- The user's Supabase is NEVER touched.
- The user's tasks are NEVER touched.
- Test cleanup only deletes task IDs the tests created.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import pytest
from pathlib import Path
from datetime import datetime, timezone

TEST_PORT = 5099
TEST_DAEMON_PORT = 5098
TEST_BASE_URL = f"http://localhost:{TEST_PORT}"

_test_server_proc = None
_test_tmpdir = None


def pytest_configure(config):
    """Spin up an isolated test server. Touches NOTHING on the user's instance."""
    global _test_server_proc, _test_tmpdir

    # Create temp dir with its own config
    _test_tmpdir = tempfile.mkdtemp(prefix="vibenode_test_")
    test_config = Path(_test_tmpdir) / "kanban_config.json"
    test_config.write_text(json.dumps({
        "kanban_backend": "sqlite",
        "kanban_depth_limit": 5,
    }, indent=2), encoding="utf-8")

    # Start test server on a separate port with its own config
    repo_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["VIBENODE_CONFIG"] = str(test_config)
    env["VIBENODE_TEST_PORT"] = str(TEST_PORT)
    env["VIBENODE_DAEMON_PORT"] = str(TEST_DAEMON_PORT)

    _test_server_proc = subprocess.Popen(
        ["python", str(repo_root / "run.py")],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )

    # Wait for it
    import urllib.request
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://localhost:{TEST_PORT}/", timeout=2)
            return
        except Exception:
            time.sleep(1)
    pytest.exit("Test server failed to start on port %d" % TEST_PORT)


def pytest_unconfigure(config):
    """Kill test server, test daemon, and clean up temp files."""
    global _test_server_proc, _test_tmpdir
    if _test_server_proc:
        _test_server_proc.terminate()
        try:
            _test_server_proc.wait(timeout=5)
        except Exception:
            _test_server_proc.kill()
        _test_server_proc = None
    # Kill the test daemon on TEST_DAEMON_PORT
    try:
        import socket as _sock
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("127.0.0.1", TEST_DAEMON_PORT))
        s.sendall(b'{"method":"shutdown"}\n')
        s.close()
    except Exception:
        pass
    if _test_tmpdir:
        shutil.rmtree(_test_tmpdir, ignore_errors=True)
        _test_tmpdir = None


def _make_session_line(msg_type, content="", timestamp=None):
    """Build a single JSONL line for a mock session file."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    if msg_type == "custom-title":
        return json.dumps({"type": "custom-title", "customTitle": content})
    return json.dumps({
        "type": msg_type,
        "message": {"content": content},
        "timestamp": ts,
    })


@pytest.fixture
def sample_session_file(tmp_path):
    """Create a single .jsonl session file with a few messages."""
    path = tmp_path / "sess_abc123.jsonl"
    lines = [
        _make_session_line("user", "Hello, help me with Python", "2026-03-01T10:00:00Z"),
        _make_session_line("assistant", "Sure! What do you need?", "2026-03-01T10:00:05Z"),
        _make_session_line("user", "Write a fibonacci function", "2026-03-01T10:01:00Z"),
        _make_session_line("assistant", "Here's a fibonacci function:\n```python\ndef fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)\n```", "2026-03-01T10:01:10Z"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def empty_session_file(tmp_path):
    """Create an empty .jsonl session file."""
    path = tmp_path / "sess_empty.jsonl"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def titled_session_file(tmp_path):
    """Create a session with a custom title."""
    path = tmp_path / "sess_titled.jsonl"
    lines = [
        _make_session_line("custom-title", "My Project"),
        _make_session_line("user", "Let's build something", "2026-03-01T12:00:00Z"),
        _make_session_line("assistant", "Sounds good!", "2026-03-01T12:00:05Z"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def mock_sessions_dir(tmp_path):
    """Create a directory with multiple session files, mimicking ~/.claude/projects/xxx/."""
    project_dir = tmp_path / "projects" / "C--Users-test-project"
    project_dir.mkdir(parents=True)

    for i in range(5):
        path = project_dir / f"session_{i:03d}.jsonl"
        lines = [
            _make_session_line("user", f"Question {i}", f"2026-03-0{i+1}T10:00:00Z"),
            _make_session_line("assistant", f"Answer {i}", f"2026-03-0{i+1}T10:00:05Z"),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Add an empty session
    empty = project_dir / "session_empty.jsonl"
    empty.write_text("", encoding="utf-8")

    # Add names file
    names = {"session_000": "First Session", "session_001": "Second Session"}
    (project_dir / "_session_names.json").write_text(json.dumps(names), encoding="utf-8")

    return project_dir


@pytest.fixture
def large_session_file(tmp_path):
    """Create a large session file (>32KB) to test head+tail reading."""
    path = tmp_path / "sess_large.jsonl"
    lines = [_make_session_line("user", "First message", "2026-01-01T00:00:00Z")]
    # Add many assistant messages to push file over 32KB
    for i in range(200):
        lines.append(_make_session_line(
            "assistant",
            f"Response {i}: " + "x" * 150,
            f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z"
        ))
    lines.append(_make_session_line("user", "Last message", "2026-01-01T12:00:00Z"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
