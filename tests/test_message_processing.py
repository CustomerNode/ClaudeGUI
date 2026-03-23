"""
Comprehensive tests for message processing and concurrency in SessionManager.

Covers:
- AssistantMessage processing (TextBlock, ToolUseBlock, ThinkingBlock, mixed)
- UserMessage processing (TextBlock, ToolResultBlock variants)
- ResultMessage processing (cost, state, errors, mismatched session_id)
- StreamEvent processing (forwarding, no log entries)
- Edge cases (empty content, unknown block types, None content)
- Multi-session independence and cross-contamination checks
- Thread safety for concurrent reads/writes
- Event loop reliability under load
- Entry accumulation and retrieval ordering
"""

import asyncio
import threading
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Mock SDK types (copied from test_session_manager.py)
# ---------------------------------------------------------------------------

class MockTextBlock:
    def __init__(self, text=""):
        self.type = "text"
        self.text = text


class MockThinkingBlock:
    def __init__(self, text=""):
        self.type = "thinking"
        self.text = text


class MockToolUseBlock:
    def __init__(self, id="tool-1", name="Bash", input=None):
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input or {}


class MockToolResultBlock:
    def __init__(self, tool_use_id="tool-1", content="", is_error=False):
        self.type = "tool_result"
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class MockAssistantMessage:
    def __init__(self, content=None):
        self.content = content or []
        self.role = "assistant"


class MockUserMessage:
    def __init__(self, content=None):
        self.content = content or []
        self.role = "user"


class MockResultMessage:
    def __init__(self, session_id="test-session", total_cost_usd=0.05,
                 duration_ms=1000, is_error=False, num_turns=1, usage=None):
        self.session_id = session_id
        self.total_cost_usd = total_cost_usd
        self.duration_ms = duration_ms
        self.is_error = is_error
        self.num_turns = num_turns
        self.usage = usage or {}


class MockStreamEvent:
    def __init__(self, event="content_block_delta", data=None):
        self.event = event
        self.data = data or {}


class MockPermissionResultAllow:
    def __init__(self, updated_input=None, updated_permissions=None):
        self.updated_input = updated_input
        self.updated_permissions = updated_permissions


class MockPermissionResultDeny:
    def __init__(self, message="", interrupt=False):
        self.message = message
        self.interrupt = interrupt


class MockToolPermissionContext:
    def __init__(self):
        self.signal = None
        self.suggestions = []


class MockClaudeSDKClient:
    """Mock SDK client that yields predefined messages."""

    def __init__(self, options=None):
        self.options = options
        self._messages = []
        self._response_messages = []
        self._connected = False
        self._queries = []
        self._interrupted = False
        self._disconnected = False
        self.connect_prompt = None

    async def connect(self, prompt=None):
        self._connected = True
        self.connect_prompt = prompt

    async def query(self, prompt, session_id="default"):
        self._queries.append(prompt)

    async def receive_messages(self):
        for msg in self._messages:
            yield msg

    async def receive_response(self):
        for msg in self._response_messages:
            yield msg

    async def interrupt(self):
        self._interrupted = True

    async def disconnect(self):
        self._disconnected = True
        self._connected = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_socketio():
    """Create a mock SocketIO instance."""
    sio = MagicMock()
    sio.emit = MagicMock()
    return sio


@pytest.fixture
def mock_sdk_types():
    """Patch all SDK types so SessionManager can be imported without the real SDK."""
    type_mocks = {
        'claude_code_sdk': MagicMock(),
        'claude_code_sdk.types': MagicMock(),
    }
    type_mocks['claude_code_sdk'].ClaudeSDKClient = MockClaudeSDKClient
    type_mocks['claude_code_sdk'].ClaudeCodeOptions = MagicMock

    types_mod = type_mocks['claude_code_sdk.types']
    types_mod.AssistantMessage = MockAssistantMessage
    types_mod.UserMessage = MockUserMessage
    types_mod.ResultMessage = MockResultMessage
    types_mod.StreamEvent = MockStreamEvent
    types_mod.TextBlock = MockTextBlock
    types_mod.ThinkingBlock = MockThinkingBlock
    types_mod.ToolUseBlock = MockToolUseBlock
    types_mod.ToolResultBlock = MockToolResultBlock
    types_mod.PermissionResultAllow = MockPermissionResultAllow
    types_mod.PermissionResultDeny = MockPermissionResultDeny
    types_mod.ContentBlock = MagicMock
    types_mod.ToolPermissionContext = MockToolPermissionContext
    types_mod.Message = MagicMock

    return type_mocks


@pytest.fixture
def sm_module(mock_sdk_types):
    """Return the reloaded session_manager module with mocked SDK types."""
    with patch.dict('sys.modules', mock_sdk_types):
        import importlib
        import app.session_manager as sm_mod
        importlib.reload(sm_mod)

        sm_mod.AssistantMessage = MockAssistantMessage
        sm_mod.UserMessage = MockUserMessage
        sm_mod.ResultMessage = MockResultMessage
        sm_mod.StreamEvent = MockStreamEvent
        sm_mod.TextBlock = MockTextBlock
        sm_mod.ThinkingBlock = MockThinkingBlock
        sm_mod.ToolUseBlock = MockToolUseBlock
        sm_mod.ToolResultBlock = MockToolResultBlock
        sm_mod.PermissionResultAllow = MockPermissionResultAllow
        sm_mod.PermissionResultDeny = MockPermissionResultDeny
        sm_mod.ClaudeSDKClient = MockClaudeSDKClient
        sm_mod.ClaudeCodeOptions = MagicMock
        yield sm_mod


@pytest.fixture
def session_manager(mock_socketio, mock_sdk_types):
    """Create a SessionManager with mocked SDK and SocketIO, started and ready."""
    with patch.dict('sys.modules', mock_sdk_types):
        import importlib
        import app.session_manager as sm_module
        importlib.reload(sm_module)

        sm_module.AssistantMessage = MockAssistantMessage
        sm_module.UserMessage = MockUserMessage
        sm_module.ResultMessage = MockResultMessage
        sm_module.StreamEvent = MockStreamEvent
        sm_module.TextBlock = MockTextBlock
        sm_module.ThinkingBlock = MockThinkingBlock
        sm_module.ToolUseBlock = MockToolUseBlock
        sm_module.ToolResultBlock = MockToolResultBlock
        sm_module.PermissionResultAllow = MockPermissionResultAllow
        sm_module.PermissionResultDeny = MockPermissionResultDeny
        sm_module.ClaudeSDKClient = MockClaudeSDKClient
        sm_module.ClaudeCodeOptions = MagicMock

        manager = sm_module.SessionManager()
        manager.start(mock_socketio)
        yield manager
        manager.stop()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def wait_for(condition, timeout=5.0, interval=0.05):
    """Poll until condition() is truthy or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"Condition not met within {timeout}s")


def _run_session(session_manager, sm_module, sid, messages, prompt="test"):
    """Helper: start a session with given messages and wait for IDLE."""
    mock_client = MockClaudeSDKClient()
    mock_client._messages = messages

    with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
        result = session_manager.start_session(sid, prompt=prompt, cwd="/tmp")
        assert result["ok"] is True
        wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    return mock_client


# ===========================================================================
# PART 1: MESSAGE PROCESSING TESTS (20+)
# ===========================================================================


class TestAssistantMessageProcessing:
    """Tests for AssistantMessage with various block types."""

    def test_text_block_creates_asst_entry(self, session_manager, sm_module):
        """TextBlock in AssistantMessage creates a kind='asst' entry with the text."""
        sid = "asst-text-basic"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("Hello, world!")]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        asst = [e for e in entries if e["kind"] == "asst"]
        assert len(asst) == 1
        assert asst[0]["text"] == "Hello, world!"

    def test_text_block_long_text_truncated_to_3000(self, session_manager, sm_module):
        """TextBlock with text > 3000 chars is truncated to 3000."""
        sid = "asst-text-long"
        long_text = "A" * 5000
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock(long_text)]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        asst = [e for e in entries if e["kind"] == "asst"]
        assert len(asst) == 1
        assert len(asst[0]["text"]) == 3000

    def test_tool_use_block_creates_tool_use_entry(self, session_manager, sm_module):
        """ToolUseBlock creates kind='tool_use' with name, desc, id."""
        sid = "asst-tool-use"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockToolUseBlock(id="tu-abc", name="Read", input={"path": "/tmp/file.txt"})
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"
        assert tools[0]["id"] == "tu-abc"
        assert "/tmp/file.txt" in tools[0]["desc"]

    def test_tool_use_command_input_shows_command(self, session_manager, sm_module):
        """ToolUseBlock with 'command' input shows the command as desc."""
        sid = "asst-tool-cmd"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockToolUseBlock(id="tu-1", name="Bash", input={"command": "ls -la /home"})
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert tools[0]["desc"] == "ls -la /home"

    def test_tool_use_file_path_input_shows_path(self, session_manager, sm_module):
        """ToolUseBlock with 'path' input shows the path as desc."""
        sid = "asst-tool-path"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockToolUseBlock(id="tu-2", name="Write", input={"path": "/src/main.py", "content": "print('hi')"})
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert "/src/main.py" in tools[0]["desc"]
        assert "write" in tools[0]["desc"].lower()

    def test_tool_use_pattern_input_shows_pattern(self, session_manager, sm_module):
        """ToolUseBlock with 'pattern' input shows the pattern as desc."""
        sid = "asst-tool-pattern"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockToolUseBlock(id="tu-3", name="Glob", input={"pattern": "**/*.py"})
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert tools[0]["desc"] == "**/*.py"

    def test_tool_use_other_input_shows_first_key_value(self, session_manager, sm_module):
        """ToolUseBlock with other input shows first key:value as desc."""
        sid = "asst-tool-other"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockToolUseBlock(id="tu-4", name="WebSearch", input={"query": "python docs"})
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert "query" in tools[0]["desc"]
        assert "python docs" in tools[0]["desc"]

    def test_tool_use_empty_input_desc_empty(self, session_manager, sm_module):
        """ToolUseBlock with empty input gives empty desc."""
        sid = "asst-tool-empty"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockToolUseBlock(id="tu-5", name="SomeTool", input={})
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert len(tools) == 1
        assert tools[0].get("desc", "") == ""

    def test_thinking_block_skipped(self, session_manager, sm_module):
        """ThinkingBlock should not create any entry."""
        sid = "asst-thinking"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockThinkingBlock("Internal reasoning...")]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        # No entries from the thinking block; only the result doesn't add entries
        # when is_error=False
        assert all(e.get("text", "") != "Internal reasoning..." for e in entries)

    def test_multiple_blocks_in_one_message(self, session_manager, sm_module):
        """Multiple blocks in one AssistantMessage create multiple entries."""
        sid = "asst-multi-block"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockTextBlock("First paragraph"),
                MockTextBlock("Second paragraph"),
                MockToolUseBlock(id="tu-m1", name="Bash", input={"command": "echo hi"}),
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        asst = [e for e in entries if e["kind"] == "asst"]
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert len(asst) == 2
        assert len(tools) == 1
        assert asst[0]["text"] == "First paragraph"
        assert asst[1]["text"] == "Second paragraph"

    def test_mixed_text_and_tool_use_blocks(self, session_manager, sm_module):
        """Mixed TextBlock and ToolUseBlock in one message both produce entries."""
        sid = "asst-mixed"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([
                MockTextBlock("Let me run a command"),
                MockToolUseBlock(id="tu-mix", name="Bash", input={"command": "pwd"}),
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        kinds = [e["kind"] for e in entries]
        assert "asst" in kinds
        assert "tool_use" in kinds


class TestUserMessageProcessing:
    """Tests for UserMessage processing."""

    def test_text_block_creates_user_entry(self, session_manager, sm_module):
        """TextBlock in UserMessage creates a kind='user' entry."""
        sid = "user-text"
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([MockTextBlock("User says hello")]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        user = [e for e in entries if e["kind"] == "user"]
        assert len(user) == 2  # 1 from start_session prompt + 1 from SDK UserMessage
        assert user[1]["text"] == "User says hello"

    def test_text_block_long_text_truncated_to_2000(self, session_manager, sm_module):
        """UserMessage TextBlock with text > 2000 chars is truncated to 2000."""
        sid = "user-text-long"
        long_text = "B" * 4000
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([MockTextBlock(long_text)]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        user = [e for e in entries if e["kind"] == "user"]
        assert len(user) == 2  # 1 from start_session prompt + 1 from SDK UserMessage
        assert len(user[1]["text"]) == 2000

    def test_tool_result_block_creates_entry(self, session_manager, sm_module):
        """ToolResultBlock in UserMessage creates kind='tool_result' with text and tool_use_id."""
        sid = "user-tool-result"
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([
                MockToolResultBlock(tool_use_id="tu-ref", content="Operation completed")
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        results = [e for e in entries if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert "Operation completed" in results[0]["text"]
        assert results[0]["tool_use_id"] == "tu-ref"

    def test_tool_result_is_error_true(self, session_manager, sm_module):
        """ToolResultBlock with is_error=True sets is_error on the entry."""
        sid = "user-tool-error"
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([
                MockToolResultBlock(tool_use_id="tu-err", content="Command failed", is_error=True)
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        results = [e for e in entries if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["is_error"] is True

    def test_tool_result_list_content(self, session_manager, sm_module):
        """ToolResultBlock with list content extracts text from text-type dicts."""
        sid = "user-tool-list"
        list_content = [
            {"type": "text", "text": "Line 1"},
            {"type": "text", "text": "Line 2"},
        ]
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([
                MockToolResultBlock(tool_use_id="tu-list", content=list_content)
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        results = [e for e in entries if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert "Line 1" in results[0]["text"]
        assert "Line 2" in results[0]["text"]

    def test_tool_result_dict_content_stringified(self, session_manager, sm_module):
        """ToolResultBlock with dict content is stringified."""
        sid = "user-tool-dict"
        dict_content = {"status": "ok", "count": 42}
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([
                MockToolResultBlock(tool_use_id="tu-dict", content=dict_content)
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        results = [e for e in entries if e["kind"] == "tool_result"]
        assert len(results) == 1
        # dict is stringified via str()
        assert "status" in results[0]["text"]


class TestResultMessageProcessing:
    """Tests for ResultMessage processing."""

    def test_sets_cost_from_total_cost_usd(self, session_manager, sm_module):
        """ResultMessage sets cost_usd from total_cost_usd."""
        sid = "result-cost"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("Done")]),
            MockResultMessage(session_id=sid, total_cost_usd=0.0456),
        ])

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.cost_usd == pytest.approx(0.0456)

    def test_sets_state_to_idle(self, session_manager, sm_module):
        """ResultMessage sets state to IDLE."""
        sid = "result-idle"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("Done")]),
            MockResultMessage(session_id=sid),
        ])

        assert session_manager.get_session_state(sid) == "idle"

    def test_is_error_adds_error_entry(self, session_manager, sm_module):
        """ResultMessage with is_error=True adds an error system entry."""
        sid = "result-error"
        _run_session(session_manager, sm_module, sid, [
            MockResultMessage(session_id=sid, is_error=True),
        ])

        entries = session_manager.get_entries(sid)
        error_entries = [e for e in entries if e.get("is_error")]
        assert len(error_entries) >= 1
        assert "error" in error_entries[0]["text"].lower()

    def test_session_id_mismatch_logged_no_crash(self, session_manager, sm_module):
        """ResultMessage with mismatched session_id doesn't crash."""
        sid = "result-mismatch"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("Ok")]),
            MockResultMessage(session_id="completely-different-id", total_cost_usd=0.01),
        ])

        # Should still reach idle and have the cost set
        assert session_manager.get_session_state(sid) == "idle"
        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.cost_usd == pytest.approx(0.01)

    def test_missing_fields_handled_gracefully(self, session_manager, sm_module):
        """ResultMessage with missing optional fields doesn't crash."""
        sid = "result-missing"

        # Create a ResultMessage with no total_cost_usd attribute
        class BareResultMessage:
            pass

        # For isinstance check we need it to match ResultMessage
        bare = MockResultMessage.__new__(MockResultMessage)
        # Don't set total_cost_usd, is_error, session_id -- rely on getattr defaults
        # Actually let's properly test by removing attributes
        msg = MockResultMessage(session_id=sid)
        del msg.total_cost_usd
        del msg.is_error

        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("Ok")]),
            msg,
        ])

        assert session_manager.get_session_state(sid) == "idle"
        with session_manager._lock:
            info = session_manager._sessions[sid]
        # cost defaults to 0.0 when attr is missing
        assert info.cost_usd == 0.0


class TestStreamEventProcessing:
    """Tests for StreamEvent processing."""

    def test_stream_event_emitted_via_websocket(self, session_manager, sm_module, mock_socketio):
        """StreamEvent is forwarded as a 'stream_event' WebSocket event."""
        sid = "stream-ws"
        _run_session(session_manager, sm_module, sid, [
            MockStreamEvent(event="content_block_delta", data={"delta": "text chunk"}),
            MockAssistantMessage([MockTextBlock("Final text")]),
            MockResultMessage(session_id=sid),
        ])

        # Check that socketio.emit was called with 'stream_event'
        stream_calls = [
            c for c in mock_socketio.emit.call_args_list
            if c[0][0] == 'stream_event'
        ]
        assert len(stream_calls) >= 1
        payload = stream_calls[0][0][1]
        assert payload['session_id'] == sid
        assert 'event' in payload

    def test_stream_event_with_event_dict_forwarded(self, session_manager, sm_module, mock_socketio):
        """StreamEvent with event dict is forwarded correctly."""
        sid = "stream-dict"
        evt_data = {"type": "content_block_delta", "index": 0}
        _run_session(session_manager, sm_module, sid, [
            MockStreamEvent(event=evt_data, data={"delta": "some text"}),
            MockResultMessage(session_id=sid),
        ])

        stream_calls = [
            c for c in mock_socketio.emit.call_args_list
            if c[0][0] == 'stream_event'
        ]
        assert len(stream_calls) >= 1
        payload = stream_calls[0][0][1]
        assert payload['event']['event'] == evt_data

    def test_stream_event_no_log_entries(self, session_manager, sm_module):
        """StreamEvents do not create log entries in the session."""
        sid = "stream-no-entry"
        _run_session(session_manager, sm_module, sid, [
            MockStreamEvent(event="content_block_start"),
            MockStreamEvent(event="content_block_delta"),
            MockStreamEvent(event="content_block_stop"),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        # Only entries that might exist are from ResultMessage (none if is_error=False)
        stream_entries = [e for e in entries if e["kind"] == "stream"]
        assert len(stream_entries) == 0


class TestEdgeCases:
    """Edge case tests for message processing."""

    def test_message_with_no_content_blocks(self, session_manager, sm_module):
        """AssistantMessage with empty content list doesn't crash."""
        sid = "edge-no-content"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage(content=[]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        # No asst entries since there were no blocks
        asst = [e for e in entries if e["kind"] == "asst"]
        assert len(asst) == 0

    def test_message_with_unknown_block_type(self, session_manager, sm_module):
        """Unknown block types are silently skipped."""
        sid = "edge-unknown-block"

        class UnknownBlock:
            def __init__(self):
                self.type = "unknown_block_type"
                self.text = "mystery"

        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage(content=[UnknownBlock()]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        # Unknown block should not create any entry
        assert all(e.get("text", "") != "mystery" for e in entries)

    def test_null_content_no_crash(self, session_manager, sm_module):
        """AssistantMessage with None content doesn't crash."""
        sid = "edge-none-content"

        msg = MockAssistantMessage(content=None)
        # The code does: message.content if hasattr(message, 'content') else []
        # content is None, iterating over None would fail -- but the code
        # checks hasattr then iterates. Since None is not iterable, we need to
        # verify the code handles this. Looking at the code: it does
        # `for block in (message.content if hasattr(message, 'content') else []):`
        # If content is None, this will try `for block in None` which raises TypeError.
        # But the _drive_session has a broad except. Let's verify no crash.
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [msg, MockResultMessage(session_id=sid)]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            result = session_manager.start_session(sid, prompt="test", cwd="/tmp")
            assert result["ok"] is True
            # Wait for session to settle -- it may error and go to stopped,
            # or it may handle None content gracefully
            wait_for(
                lambda: session_manager.get_session_state(sid) in ("idle", "stopped"),
                timeout=5,
            )

        # Key assertion: no unhandled crash, session still tracked
        assert session_manager.has_session(sid)

    def test_user_message_with_no_content(self, session_manager, sm_module):
        """UserMessage with empty content list doesn't crash."""
        sid = "edge-user-no-content"
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage(content=[]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        user = [e for e in entries if e["kind"] == "user"]
        assert len(user) == 1  # 1 from start_session prompt, 0 from empty SDK UserMessage

    def test_tool_result_with_empty_string_content(self, session_manager, sm_module):
        """ToolResultBlock with empty string content handles gracefully."""
        sid = "edge-tool-empty-str"
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([
                MockToolResultBlock(tool_use_id="tu-empty", content="")
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        results = [e for e in entries if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["tool_use_id"] == "tu-empty"


# ===========================================================================
# PART 2: CONCURRENCY TESTS (20+)
# ===========================================================================


class TestMultiSessionIndependence:
    """Tests for multi-session independence."""

    def test_five_sessions_simultaneous_own_entries(self, session_manager, sm_module):
        """5 sessions running simultaneously each get their own entries."""
        sids = [f"multi-{i}" for i in range(5)]
        clients = []

        for i, sid in enumerate(sids):
            client = MockClaudeSDKClient()
            client._messages = [
                MockAssistantMessage([MockTextBlock(f"Response from session {i}")]),
                MockResultMessage(session_id=sid, total_cost_usd=0.01 * (i + 1)),
            ]
            clients.append(client)

        client_iter = iter(clients)

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(client_iter)):
            for sid in sids:
                session_manager.start_session(sid, prompt=f"prompt for {sid}", cwd="/tmp")

            for sid in sids:
                wait_for(lambda s=sid: session_manager.get_session_state(s) == "idle", timeout=10)

        for i, sid in enumerate(sids):
            entries = session_manager.get_entries(sid)
            asst = [e for e in entries if e["kind"] == "asst"]
            assert len(asst) >= 1
            assert f"Response from session {i}" in asst[0]["text"]

    def test_start_stop_rapidly_no_cross_contamination(self, session_manager, sm_module):
        """Starting and stopping sessions rapidly doesn't cross-contaminate entries."""
        for i in range(5):
            sid = f"rapid-{i}"
            client = MockClaudeSDKClient()
            client._messages = [
                MockAssistantMessage([MockTextBlock(f"Unique-{i}")]),
                MockResultMessage(session_id=sid),
            ]

            with patch.object(sm_module, 'ClaudeSDKClient', return_value=client):
                session_manager.start_session(sid, prompt="go", cwd="/tmp")
                wait_for(lambda s=sid: session_manager.get_session_state(s) == "idle", timeout=5)

            entries = session_manager.get_entries(sid)
            asst = [e for e in entries if e["kind"] == "asst"]
            # Every entry should only contain this session's unique text
            for e in asst:
                assert f"Unique-{i}" in e["text"]

            session_manager.close_session(sid)
            wait_for(lambda s=sid: session_manager.get_session_state(s) == "stopped", timeout=5)

    def test_close_one_session_others_unaffected(self, session_manager, sm_module):
        """Closing one session doesn't affect other running sessions."""
        sid_a = "close-a"
        sid_b = "close-b"

        client_a = MockClaudeSDKClient()
        client_a._messages = [
            MockAssistantMessage([MockTextBlock("A response")]),
            MockResultMessage(session_id=sid_a),
        ]
        client_b = MockClaudeSDKClient()
        client_b._messages = [
            MockAssistantMessage([MockTextBlock("B response")]),
            MockResultMessage(session_id=sid_b),
        ]

        clients = iter([client_a, client_b])

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(clients)):
            session_manager.start_session(sid_a, prompt="a", cwd="/tmp")
            session_manager.start_session(sid_b, prompt="b", cwd="/tmp")

            wait_for(lambda: session_manager.get_session_state(sid_a) == "idle")
            wait_for(lambda: session_manager.get_session_state(sid_b) == "idle")

        # Close session A
        session_manager.close_session(sid_a)
        wait_for(lambda: session_manager.get_session_state(sid_a) == "stopped")

        # Session B should still be idle with its entries intact
        assert session_manager.get_session_state(sid_b) == "idle"
        entries_b = session_manager.get_entries(sid_b)
        asst_b = [e for e in entries_b if e["kind"] == "asst"]
        assert any("B response" in e["text"] for e in asst_b)

    def test_permission_in_A_message_in_B_independent(self, session_manager, sm_module):
        """Permission request in session A and message processing in B are independent."""
        sid_a = "perm-a"
        sid_b = "msg-b"

        # Session A: set to WAITING with pending permission
        info_a = sm_module.SessionInfo(session_id=sid_a, state=sm_module.SessionState.WAITING)
        created = [None]
        event = threading.Event()

        def _create():
            created[0] = session_manager._loop.create_future()
            event.set()

        session_manager._loop.call_soon_threadsafe(_create)
        event.wait(timeout=5)
        info_a.pending_permission = created[0]
        info_a.pending_tool_name = "Bash"

        with session_manager._lock:
            session_manager._sessions[sid_a] = info_a

        # Session B: run normally
        client_b = MockClaudeSDKClient()
        client_b._messages = [
            MockAssistantMessage([MockTextBlock("B is fine")]),
            MockResultMessage(session_id=sid_b),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=client_b):
            session_manager.start_session(sid_b, prompt="go b", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid_b) == "idle", timeout=5)

        # Session A should still be waiting
        assert session_manager.get_session_state(sid_a) == "waiting"
        # Session B should be idle with entries
        entries_b = session_manager.get_entries(sid_b)
        assert any(e["kind"] == "asst" for e in entries_b)


class TestThreadSafety:
    """Tests for thread safety under concurrent access."""

    def test_get_entries_while_appending(self, session_manager, sm_module):
        """get_entries while entries are being appended doesn't crash."""
        sid = "ts-append"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WORKING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        errors = []
        stop_event = threading.Event()

        def append_entries():
            for i in range(200):
                if stop_event.is_set():
                    break
                entry = sm_module.LogEntry(kind="asst", text=f"entry-{i}")
                with info._lock:
                    info.entries.append(entry)
                time.sleep(0.001)

        def read_entries():
            for _ in range(100):
                if stop_event.is_set():
                    break
                try:
                    session_manager.get_entries(sid)
                except Exception as e:
                    errors.append(e)
                time.sleep(0.002)

        t_write = threading.Thread(target=append_entries)
        t_read = threading.Thread(target=read_entries)
        t_write.start()
        t_read.start()
        t_read.join(timeout=10)
        stop_event.set()
        t_write.join(timeout=10)

        assert len(errors) == 0, f"Read errors: {errors}"

    def test_get_all_states_while_sessions_changing(self, session_manager, sm_module):
        """get_all_states while sessions are starting/stopping gives consistent snapshot."""
        errors = []

        def add_sessions():
            for i in range(20):
                sid = f"ts-state-{i}"
                info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.IDLE)
                with session_manager._lock:
                    session_manager._sessions[sid] = info
                time.sleep(0.005)

        def read_states():
            for _ in range(30):
                try:
                    states = session_manager.get_all_states()
                    # Each state dict should have a valid session_id
                    for s in states:
                        assert "session_id" in s
                        assert "state" in s
                except Exception as e:
                    errors.append(e)
                time.sleep(0.003)

        t_add = threading.Thread(target=add_sessions)
        t_read = threading.Thread(target=read_states)
        t_add.start()
        t_read.start()
        t_add.join(timeout=10)
        t_read.join(timeout=10)

        assert len(errors) == 0, f"State read errors: {errors}"

    def test_start_session_from_multiple_threads_no_duplicates(self, session_manager, sm_module):
        """Starting the same session from multiple threads - one succeeds, others fail."""
        sid = "ts-dup-start"
        results = []

        client = MockClaudeSDKClient()
        client._messages = [
            MockAssistantMessage([MockTextBlock("Hi")]),
            MockResultMessage(session_id=sid),
        ]

        def try_start():
            with patch.object(sm_module, 'ClaudeSDKClient', return_value=client):
                r = session_manager.start_session(sid, prompt="go", cwd="/tmp")
                results.append(r)

        threads = [threading.Thread(target=try_start) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Exactly one should succeed, the rest should fail with "already running"
        ok_count = sum(1 for r in results if r["ok"])
        fail_count = sum(1 for r in results if not r["ok"])
        assert ok_count == 1
        assert fail_count == 4

    def test_resolve_permission_while_event_loop_processes(self, session_manager, sm_module):
        """resolve_permission from main thread while event loop is active -- no deadlock."""
        sid = "ts-resolve"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WAITING)

        created = [None]
        event = threading.Event()

        def _create():
            created[0] = session_manager._loop.create_future()
            event.set()

        session_manager._loop.call_soon_threadsafe(_create)
        event.wait(timeout=5)
        info.pending_permission = created[0]
        info.pending_tool_name = "Write"

        with session_manager._lock:
            session_manager._sessions[sid] = info

        # This must not deadlock
        result = session_manager.resolve_permission(sid, allow=True)
        assert result["ok"] is True

        wait_for(lambda: created[0].done(), timeout=3)
        perm_result, always = created[0].result()
        assert isinstance(perm_result, MockPermissionResultAllow)

    def test_close_session_while_permission_pending(self, session_manager, sm_module):
        """Closing a session while permission is pending cancels the permission."""
        sid = "ts-close-perm"

        # Create a mock client and session info manually
        mock_client = MockClaudeSDKClient()
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.WAITING,
            client=mock_client,
        )

        created = [None]
        event = threading.Event()

        def _create():
            created[0] = session_manager._loop.create_future()
            event.set()

        session_manager._loop.call_soon_threadsafe(_create)
        event.wait(timeout=5)
        info.pending_permission = created[0]
        info.pending_tool_name = "Bash"

        with session_manager._lock:
            session_manager._sessions[sid] = info

        # Close the session
        result = session_manager.close_session(sid)
        assert result["ok"] is True

        wait_for(lambda: session_manager.get_session_state(sid) == "stopped", timeout=5)

        # Permission future should have been resolved (with deny)
        assert created[0].done()

    def test_send_message_while_close_session(self, session_manager, sm_module):
        """send_message while close_session is in progress -- one succeeds, one fails gracefully."""
        sid = "ts-send-close"

        client = MockClaudeSDKClient()
        client._messages = [
            MockAssistantMessage([MockTextBlock("Ready")]),
            MockResultMessage(session_id=sid),
        ]
        client._response_messages = [
            MockAssistantMessage([MockTextBlock("Follow-up")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=client):
            session_manager.start_session(sid, prompt="init", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle", timeout=5)

        # Try both operations -- at least one should succeed or fail gracefully
        r_close = session_manager.close_session(sid)
        r_send = session_manager.send_message(sid, "hello")

        # Both calls return a dict with "ok" key (no crash)
        assert "ok" in r_close
        assert "ok" in r_send

        # Eventually reaches a terminal state
        wait_for(
            lambda: session_manager.get_session_state(sid) in ("idle", "stopped"),
            timeout=5,
        )


class TestEventLoopReliability:
    """Tests for event loop reliability under load."""

    def test_100_messages_no_backpressure(self, session_manager, sm_module):
        """Event loop processes 100 messages without backpressure issues."""
        sid = "loop-100"
        messages = []
        for i in range(100):
            messages.append(MockAssistantMessage([MockTextBlock(f"Msg {i}")]))
        messages.append(MockResultMessage(session_id=sid))

        _run_session(session_manager, sm_module, sid, messages)

        entries = session_manager.get_entries(sid)
        asst = [e for e in entries if e["kind"] == "asst"]
        assert len(asst) == 100

    def test_large_message_3000_plus_chars(self, session_manager, sm_module):
        """Large message (3000+ chars) is handled and truncated without memory issues."""
        sid = "loop-large"
        big_text = "X" * 10000  # much larger than 3000 limit
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock(big_text)]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        asst = [e for e in entries if e["kind"] == "asst"]
        assert len(asst) == 1
        assert len(asst[0]["text"]) == 3000

    def test_rapid_start_stop_10_cycles(self, session_manager, sm_module):
        """Rapid session start/stop cycles (10x) all clean up properly."""
        for i in range(10):
            sid = f"cycle-{i}"
            client = MockClaudeSDKClient()
            client._messages = [
                MockAssistantMessage([MockTextBlock(f"Cycle {i}")]),
                MockResultMessage(session_id=sid),
            ]

            with patch.object(sm_module, 'ClaudeSDKClient', return_value=client):
                session_manager.start_session(sid, prompt="go", cwd="/tmp")
                wait_for(
                    lambda s=sid: session_manager.get_session_state(s) == "idle",
                    timeout=5,
                )

            session_manager.close_session(sid)
            wait_for(
                lambda s=sid: session_manager.get_session_state(s) == "stopped",
                timeout=5,
            )

        # All sessions should be stopped
        for i in range(10):
            sid = f"cycle-{i}"
            assert session_manager.get_session_state(sid) == "stopped"

    def test_session_manager_stop_cleans_up_all(self, sm_module, mock_socketio):
        """SessionManager.stop() cleans up all sessions."""
        with patch.dict('sys.modules', {
            'claude_code_sdk': MagicMock(),
            'claude_code_sdk.types': MagicMock(),
        }):
            manager = sm_module.SessionManager()
            manager.start(mock_socketio)

            # Add a few sessions manually
            for i in range(3):
                sid = f"cleanup-{i}"
                info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.IDLE)
                with manager._lock:
                    manager._sessions[sid] = info

            manager.stop()

            assert manager._started is False


class TestEntryAccumulationAndRetrieval:
    """Tests for entry accumulation and retrieval."""

    def test_get_entries_returns_all_in_order(self, session_manager, sm_module):
        """get_entries returns all entries in insertion order."""
        sid = "entries-order"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("First")]),
            MockUserMessage([MockToolResultBlock(tool_use_id="t1", content="Second")]),
            MockAssistantMessage([MockTextBlock("Third")]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        texts = [e.get("text", "") for e in entries]
        # entries[0] is the user prompt from start_session; SDK entries start at [1]
        assert texts[1] == "First"
        assert texts[2] == "Second"
        assert texts[3] == "Third"

    def test_get_entries_since_N_returns_after_index(self, session_manager, sm_module):
        """get_entries(since=N) returns only entries after index N."""
        sid = "entries-since"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.IDLE)
        for i in range(5):
            info.entries.append(sm_module.LogEntry(kind="asst", text=f"entry-{i}"))
        with session_manager._lock:
            session_manager._sessions[sid] = info

        since_0 = session_manager.get_entries(sid, since=0)
        assert len(since_0) == 5

        since_3 = session_manager.get_entries(sid, since=3)
        assert len(since_3) == 2
        assert since_3[0]["text"] == "entry-3"
        assert since_3[1]["text"] == "entry-4"

        since_5 = session_manager.get_entries(sid, since=5)
        assert len(since_5) == 0

    def test_get_entries_after_session_restart_fresh(self, session_manager, sm_module):
        """After restarting a stopped session, entries are fresh."""
        sid = "entries-restart"

        # First run
        client1 = MockClaudeSDKClient()
        client1._messages = [
            MockAssistantMessage([MockTextBlock("Old response")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=client1):
            session_manager.start_session(sid, prompt="first", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle", timeout=5)

        session_manager.close_session(sid)
        wait_for(lambda: session_manager.get_session_state(sid) == "stopped", timeout=5)

        old_entries = session_manager.get_entries(sid)

        # Second run (restart)
        client2 = MockClaudeSDKClient()
        client2._messages = [
            MockAssistantMessage([MockTextBlock("New response")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=client2):
            session_manager.start_session(sid, prompt="second", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle", timeout=5)

        new_entries = session_manager.get_entries(sid)
        # New entries should not contain old response
        asst = [e for e in new_entries if e["kind"] == "asst"]
        assert all("Old response" not in e["text"] for e in asst)
        assert any("New response" in e["text"] for e in asst)

    def test_entry_indices_in_websocket_monotonically_increasing(self, session_manager, sm_module, mock_socketio):
        """Entry indices in WebSocket events are monotonically increasing."""
        sid = "entries-idx"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("A")]),
            MockAssistantMessage([MockTextBlock("B")]),
            MockAssistantMessage([MockTextBlock("C")]),
            MockResultMessage(session_id=sid),
        ])

        # Extract all session_entry emissions
        entry_calls = [
            c for c in mock_socketio.emit.call_args_list
            if c[0][0] == 'session_entry' and c[0][1].get('session_id') == sid
        ]
        indices = [c[0][1]['index'] for c in entry_calls]

        # Indices should be strictly increasing
        for i in range(1, len(indices)):
            assert indices[i] > indices[i - 1], f"Index {indices[i]} not > {indices[i-1]}"

    def test_get_entries_with_since_beyond_length(self, session_manager, sm_module):
        """get_entries with since > len(entries) returns empty list."""
        sid = "entries-beyond"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.IDLE)
        info.entries.append(sm_module.LogEntry(kind="asst", text="only one"))
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.get_entries(sid, since=100)
        assert result == []

    def test_entries_include_timestamp(self, session_manager, sm_module):
        """All entries include a timestamp."""
        sid = "entries-ts"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("Hi")]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        for entry in entries:
            assert "timestamp" in entry
            assert isinstance(entry["timestamp"], float)

    def test_tool_result_text_truncated_to_600(self, session_manager, sm_module):
        """ToolResultBlock text is truncated to 600 characters."""
        sid = "entries-tr-trunc"
        long_result = "R" * 1000
        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([
                MockToolResultBlock(tool_use_id="tu-trunc", content=long_result)
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        results = [e for e in entries if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert len(results[0]["text"]) == 600


class TestAdditionalConcurrency:
    """Additional concurrency edge case tests."""

    def test_multiple_sessions_different_costs(self, session_manager, sm_module):
        """Each session tracks its own cost independently."""
        costs = [0.01, 0.05, 0.10]
        sids = [f"cost-{i}" for i in range(3)]
        clients = []

        for i, sid in enumerate(sids):
            client = MockClaudeSDKClient()
            client._messages = [
                MockAssistantMessage([MockTextBlock(f"Cost test {i}")]),
                MockResultMessage(session_id=sid, total_cost_usd=costs[i]),
            ]
            clients.append(client)

        client_iter = iter(clients)

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(client_iter)):
            for sid in sids:
                session_manager.start_session(sid, prompt="go", cwd="/tmp")

            for sid in sids:
                wait_for(lambda s=sid: session_manager.get_session_state(s) == "idle", timeout=10)

        for i, sid in enumerate(sids):
            with session_manager._lock:
                info = session_manager._sessions[sid]
            assert info.cost_usd == pytest.approx(costs[i])

    def test_concurrent_get_entries_different_sessions(self, session_manager, sm_module):
        """Concurrent get_entries calls on different sessions don't interfere."""
        sids = [f"conc-get-{i}" for i in range(5)]
        for i, sid in enumerate(sids):
            info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.IDLE)
            for j in range(10):
                info.entries.append(sm_module.LogEntry(kind="asst", text=f"s{i}-e{j}"))
            with session_manager._lock:
                session_manager._sessions[sid] = info

        errors = []
        results_map = {}

        def read_session(sid_local):
            try:
                entries = session_manager.get_entries(sid_local)
                results_map[sid_local] = entries
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_session, args=(sid,)) for sid in sids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0
        for i, sid in enumerate(sids):
            entries = results_map[sid]
            assert len(entries) == 10
            # Verify entries belong to this session
            for j, e in enumerate(entries):
                assert e["text"] == f"s{i}-e{j}"

    def test_has_session_and_get_state_concurrent(self, session_manager, sm_module):
        """has_session and get_session_state called concurrently with add/remove."""
        errors = []
        stop_event = threading.Event()

        def mutate_sessions():
            for i in range(50):
                if stop_event.is_set():
                    break
                sid = f"conc-mut-{i}"
                info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.IDLE)
                with session_manager._lock:
                    session_manager._sessions[sid] = info
                time.sleep(0.002)
                with session_manager._lock:
                    del session_manager._sessions[sid]

        def read_sessions():
            for _ in range(100):
                if stop_event.is_set():
                    break
                try:
                    session_manager.has_session("conc-mut-0")
                    session_manager.get_session_state("conc-mut-0")
                    session_manager.get_all_states()
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        t_mut = threading.Thread(target=mutate_sessions)
        t_read = threading.Thread(target=read_sessions)
        t_mut.start()
        t_read.start()
        t_read.join(timeout=10)
        stop_event.set()
        t_mut.join(timeout=10)

        assert len(errors) == 0, f"Concurrent errors: {errors}"

    def test_websocket_emit_called_for_every_entry(self, session_manager, sm_module, mock_socketio):
        """Every log entry triggers a WebSocket session_entry emit."""
        sid = "ws-emit"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("A"), MockTextBlock("B")]),
            MockUserMessage([MockToolResultBlock(tool_use_id="t1", content="C")]),
            MockResultMessage(session_id=sid),
        ])

        entry_calls = [
            c for c in mock_socketio.emit.call_args_list
            if c[0][0] == 'session_entry' and c[0][1].get('session_id') == sid
        ]
        # 1 user prompt + 2 asst + 1 tool_result = 4 entries
        assert len(entry_calls) == 4

    def test_websocket_state_emissions_on_session_lifecycle(self, session_manager, sm_module, mock_socketio):
        """Session lifecycle emits state changes via WebSocket."""
        sid = "ws-state"
        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([MockTextBlock("Hi")]),
            MockResultMessage(session_id=sid),
        ])

        state_calls = [
            c for c in mock_socketio.emit.call_args_list
            if c[0][0] == 'session_state'
            and c[0][1].get('session_id') == sid
        ]
        # At minimum: STARTING, WORKING, IDLE (from ResultMessage), IDLE (from _drive_session exit)
        assert len(state_calls) >= 3
        states = [c[0][1]['state'] for c in state_calls]
        assert 'starting' in states
        assert 'working' in states
        assert 'idle' in states

    def test_tool_use_block_with_non_dict_input(self, session_manager, sm_module):
        """ToolUseBlock with non-dict input treated as empty dict for desc."""
        sid = "tool-non-dict"
        block = MockToolUseBlock(id="tu-nd", name="SomeTool")
        block.input = "not-a-dict"

        _run_session(session_manager, sm_module, sid, [
            MockAssistantMessage([block]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        tools = [e for e in entries if e["kind"] == "tool_use"]
        assert len(tools) == 1
        # desc should be empty since input is not a dict
        assert tools[0].get("desc", "") == ""

    def test_tool_result_list_with_text_attr_objects(self, session_manager, sm_module):
        """ToolResultBlock list content with objects having .text attr."""
        sid = "tool-result-obj"

        class TextObj:
            def __init__(self, text):
                self.text = text

        list_content = [TextObj("from obj 1"), TextObj("from obj 2")]

        _run_session(session_manager, sm_module, sid, [
            MockUserMessage([
                MockToolResultBlock(tool_use_id="tu-obj", content=list_content)
            ]),
            MockResultMessage(session_id=sid),
        ])

        entries = session_manager.get_entries(sid)
        results = [e for e in entries if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert "from obj 1" in results[0]["text"]
        assert "from obj 2" in results[0]["text"]

    def test_result_message_error_sets_session_error_field(self, session_manager, sm_module):
        """ResultMessage with is_error=True sets info.error string."""
        sid = "result-err-field"
        _run_session(session_manager, sm_module, sid, [
            MockResultMessage(session_id=sid, is_error=True),
        ])

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.error is not None
        assert "error" in info.error.lower()
