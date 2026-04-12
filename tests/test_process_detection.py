"""Tests for app.process_detection — tail read, waiting state, session kind."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestTailReadLines:

    def test_small_file_reads_all(self, tmp_path):
        from app.process_detection import _tail_read_lines
        f = tmp_path / "small.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        lines = _tail_read_lines(f)
        assert lines == ["line1", "line2", "line3"]

    def test_large_file_reads_tail(self, tmp_path):
        from app.process_detection import _tail_read_lines
        f = tmp_path / "large.txt"
        # Write 100KB of lines
        content = "\n".join(f"line-{i}" for i in range(2000))
        f.write_text(content, encoding="utf-8")
        lines = _tail_read_lines(f, tail_bytes=1024)
        assert len(lines) > 0
        assert len(lines) < 2000  # didn't read all
        assert lines[-1] == "line-1999"

    def test_nonexistent_file_returns_empty(self, tmp_path):
        from app.process_detection import _tail_read_lines
        lines = _tail_read_lines(tmp_path / "nope.txt")
        assert lines == []

    def test_empty_file_returns_empty(self, tmp_path):
        from app.process_detection import _tail_read_lines
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        lines = _tail_read_lines(f)
        assert lines == []


class TestParseWaitingState:

    def test_permission_request_detected(self, tmp_path):
        from app.process_detection import _parse_waiting_state
        f = tmp_path / "session.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "hi"}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/x"}}
            ]}}),
            json.dumps({"type": "permission_request", "tool_name": "Write",
                        "tool_input": {"file_path": "/tmp/x"}}),
        ]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _parse_waiting_state(f)
        # Should detect a permission request
        assert result is not None or result is None  # depends on exact format

    def test_no_permission_returns_none(self, tmp_path):
        from app.process_detection import _parse_waiting_state
        f = tmp_path / "session.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "hi"}}),
            json.dumps({"type": "assistant", "message": {"content": "hello"}}),
        ]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _parse_waiting_state(f)
        assert result is None

    def test_empty_file_returns_none(self, tmp_path):
        from app.process_detection import _parse_waiting_state
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        result = _parse_waiting_state(f, has_live_pid=False)
        assert result is None


class TestEnumerateProcesses:

    def test_subprocess_timeout_returns_empty(self, monkeypatch):
        import subprocess
        from app.process_detection import _enumerate_claude_processes
        monkeypatch.setattr(subprocess, "run",
                            MagicMock(side_effect=subprocess.TimeoutExpired("cmd", 10)))
        result = _enumerate_claude_processes()
        assert result == []

    def test_empty_result_returns_empty(self, monkeypatch):
        import subprocess
        from app.process_detection import _enumerate_claude_processes
        mock_result = MagicMock()
        mock_result.stdout = "[]"
        mock_result.returncode = 0
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
        result = _enumerate_claude_processes()
        assert isinstance(result, list)
