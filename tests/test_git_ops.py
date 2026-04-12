"""Tests for app.git_ops — git cache and sync operations."""

import subprocess
from unittest.mock import MagicMock
import pytest


class TestGitCache:

    def test_get_git_cache_returns_dict(self):
        from app.git_ops import get_git_cache
        cache = get_git_cache()
        assert isinstance(cache, dict)
        assert "ahead" in cache
        assert "behind" in cache
        assert "uncommitted" in cache


class TestDoGitSync:

    def test_sync_no_git_repo(self, tmp_path, monkeypatch):
        from app import git_ops
        monkeypatch.setattr(git_ops, "_VIBENODE_DIR", tmp_path)
        result = git_ops.do_git_sync("pull")
        assert result["ok"] is False
        assert "no git repo" in result["messages"][0].lower()

    def test_sync_pull_success(self, tmp_path, monkeypatch):
        from app import git_ops
        # Create fake .git dir
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(git_ops, "_VIBENODE_DIR", tmp_path)

        mock_run = MagicMock()
        # stash returns "No local changes"
        stash_result = MagicMock(stdout="No local changes to save", returncode=0)
        # pull returns success
        pull_result = MagicMock(stdout="Already up to date.", returncode=0, stderr="")
        # rev-list for cache update
        revlist_result = MagicMock(stdout="0\t0", returncode=0)
        status_result = MagicMock(stdout="", returncode=0)

        mock_run.side_effect = [stash_result, pull_result, revlist_result, status_result]
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = git_ops.do_git_sync("pull")
        assert result["ok"] is True
        assert any("up to date" in m.lower() for m in result["messages"])

    def test_sync_push_with_scan_pass(self, tmp_path, monkeypatch):
        from app import git_ops
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(git_ops, "_VIBENODE_DIR", tmp_path)

        # Mock the security scanner to pass (imported inside function)
        monkeypatch.setattr("app.git_scanner.scan_staged_files",
                            lambda *a, **kw: {"ok": True, "summary": "clean", "files_scanned": 1})

        mock_run = MagicMock()
        # status --porcelain (no dirty files)
        status_result = MagicMock(stdout="", returncode=0)
        # push success
        push_result = MagicMock(stdout="", returncode=0, stderr="")

        mock_run.side_effect = [status_result, push_result]
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = git_ops.do_git_sync("push")
        assert result["ok"] is True

    def test_sync_push_blocked_by_scan(self, tmp_path, monkeypatch):
        from app import git_ops
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(git_ops, "_VIBENODE_DIR", tmp_path)

        monkeypatch.setattr("app.git_scanner.scan_staged_files",
                            lambda *a, **kw: {"ok": False, "summary": "secret found",
                                        "files_scanned": 5, "findings": []})

        result = git_ops.do_git_sync("push")
        assert result["ok"] is False
        assert "scan" in str(result).lower()
