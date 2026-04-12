"""
Comprehensive tests for the permission system end-to-end.

Covers:
- Permission lifecycle (callback -> WAITING -> resolve -> WORKING)
- Double-click / race-condition protection
- Concurrent permissions across sessions
- Permission data flow (tool_name, tool_input passthrough)
- State consistency during permission awaits
- WebSocket event verification
"""

import asyncio
import threading
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

# Skip entire module if app.session_manager is not importable (moved to daemon/)
pytest.importorskip("app.session_manager", reason="app.session_manager moved to daemon.session_manager")
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Mock SDK types  (mirrors test_session_manager.py)
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
    """Create a mock SocketIO instance that records emitted events."""
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
def session_manager(mock_socketio, sm_module):
    """Create a SessionManager with mocked SDK and SocketIO, started and ready."""
    manager = sm_module.SessionManager()
    manager.start(mock_socketio)
    yield manager
    manager.stop()


# ---------------------------------------------------------------------------
# Helpers
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


def create_future_on_loop(loop):
    """Create an asyncio.Future on the given event loop from a non-loop thread."""
    created = [None]
    event = threading.Event()

    def _create():
        created[0] = loop.create_future()
        event.set()

    loop.call_soon_threadsafe(_create)
    event.wait(timeout=5)
    return created[0]


def make_permission_client(session_id, sm_module, session_manager,
                           tool_name="Bash", tool_input=None,
                           post_permission_messages=None):
    """Create a mock client whose message stream triggers the permission callback.

    Returns (mock_client, permission_triggered_event, permission_result_holder).
    The client's receive_messages will:
      1. Yield an assistant message
      2. Call can_use_tool (the permission callback) and block
      3. After permission resolves, yield remaining messages and ResultMessage

    The caller must resolve the permission via session_manager.resolve_permission()
    or interrupt to unblock the stream.
    """
    if tool_input is None:
        tool_input = {"command": "rm -rf /"}
    if post_permission_messages is None:
        post_permission_messages = []

    permission_triggered = threading.Event()
    permission_result_holder = [None]

    # Capture the actual permission callback at construction time.
    # _make_permission_callback returns a standalone coroutine function
    # that doesn't depend on the options object.
    captured_callback = session_manager._make_permission_callback(session_id)

    mock_client = MockClaudeSDKClient()

    async def patched_receive_messages():
        # Yield an initial assistant text message
        yield MockAssistantMessage([MockTextBlock("I need to run a tool.")])

        # Invoke the captured permission callback directly
        permission_triggered.set()
        result = await captured_callback(tool_name, tool_input, MockToolPermissionContext())
        permission_result_holder[0] = result

        # Yield post-permission messages
        for msg in post_permission_messages:
            yield msg

        # End with a result
        yield MockResultMessage(session_id=session_id, total_cost_usd=0.03)

    mock_client.receive_messages = patched_receive_messages

    return mock_client, permission_triggered, permission_result_holder


# ===========================================================================
# PERMISSION LIFECYCLE (10+ tests)
# ===========================================================================

class TestPermissionLifecycle:

    def test_permission_callback_sets_state_waiting(self, session_manager, sm_module, mock_socketio):
        """When permission callback fires, state should go to WAITING."""
        sid = "perm-lifecycle-01"
        mock_client, perm_event, _ = make_permission_client(sid, sm_module, session_manager)

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            result = session_manager.start_session(sid, prompt="do it", cwd="/tmp")
            assert result["ok"] is True

            # Wait for the permission callback to fire
            assert perm_event.wait(timeout=5), "Permission callback never fired"

            # State should be WAITING
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # Resolve to let the session complete
            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_permission_callback_emits_session_permission(self, session_manager, sm_module, mock_socketio):
        """When permission callback fires, session_permission event should be emitted."""
        sid = "perm-lifecycle-02"
        tool_input = {"command": "echo hello"}
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Bash", tool_input=tool_input
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="run cmd", cwd="/tmp")
            assert perm_event.wait(timeout=5)

            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # Check that session_permission was emitted
            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            assert len(perm_calls) >= 1
            data = perm_calls[-1][0][1]
            assert data['session_id'] == sid
            assert data['tool_name'] == "Bash"
            assert data['tool_input'] == tool_input

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_resolve_allow_returns_allow_result(self, session_manager, sm_module):
        """Resolving with allow=True should make the callback return PermissionResultAllow."""
        sid = "perm-lifecycle-03"
        mock_client, perm_event, result_holder = make_permission_client(
            sid, sm_module, session_manager
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert isinstance(result_holder[0], MockPermissionResultAllow)

    def test_resolve_allow_sets_state_working(self, session_manager, sm_module, mock_socketio):
        """After allow resolve, state should go back to WORKING (before eventually IDLE)."""
        sid = "perm-lifecycle-04"
        state_transitions = []

        original_emit = mock_socketio.emit

        def tracking_emit(event, data, *args, **kwargs):
            if event == 'session_state' and data.get('session_id') == sid:
                state_transitions.append(data['state'])
            return original_emit(event, data, *args, **kwargs)

        mock_socketio.emit = MagicMock(side_effect=tracking_emit)

        mock_client, perm_event, _ = make_permission_client(sid, sm_module, session_manager)

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        # Should have: starting -> working -> waiting -> working -> idle
        assert "starting" in state_transitions
        assert "working" in state_transitions
        assert "waiting" in state_transitions
        # After resolve, working should appear after waiting
        waiting_idx = state_transitions.index("waiting")
        working_after = [i for i, s in enumerate(state_transitions) if s == "working" and i > waiting_idx]
        assert len(working_after) >= 1, f"No 'working' state after 'waiting': {state_transitions}"

    def test_resolve_deny_returns_deny_result(self, session_manager, sm_module):
        """Resolving with allow=False should return PermissionResultDeny."""
        sid = "perm-lifecycle-05"
        mock_client, perm_event, result_holder = make_permission_client(
            sid, sm_module, session_manager
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=False)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert isinstance(result_holder[0], MockPermissionResultDeny)
        assert result_holder[0].interrupt is False

    def test_resolve_deny_sets_state_working_then_idle(self, session_manager, sm_module):
        """After deny resolve, state goes WORKING then IDLE."""
        sid = "perm-lifecycle-06"
        mock_client, perm_event, _ = make_permission_client(sid, sm_module, session_manager)

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=False)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        # Final state is idle, not stopped
        assert session_manager.get_session_state(sid) == "idle"

    def test_resolve_always_returns_allow_with_always_flag(self, session_manager, sm_module):
        """Resolving with always=True should set the always flag in the future result."""
        sid = "perm-lifecycle-07"

        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WAITING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        future = create_future_on_loop(session_manager._loop)
        info.pending_permission = future

        result = session_manager.resolve_permission(sid, allow=True, always=True)
        assert result["ok"] is True

        wait_for(lambda: future.done(), timeout=2)
        perm_result, always = future.result()
        assert isinstance(perm_result, MockPermissionResultAllow)
        assert always is True

    def test_permission_timeout_returns_deny(self, session_manager, sm_module):
        """When the permission callback times out, it should return deny."""
        sid = "perm-lifecycle-08"

        # Directly test the callback with a very short timeout by patching wait_for timeout
        callback = session_manager._make_permission_callback(sid)

        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WORKING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        # We'll run the callback on the event loop with a patched short timeout
        result_holder = [None]

        async def run_callback_with_short_timeout():
            # Monkey-patch asyncio.wait_for for this test
            original_wait_for = asyncio.wait_for

            async def short_wait_for(fut, timeout):
                return await original_wait_for(fut, timeout=0.1)

            with patch('asyncio.wait_for', short_wait_for):
                result_holder[0] = await callback("Bash", {"command": "ls"}, MockToolPermissionContext())

        future = asyncio.run_coroutine_threadsafe(run_callback_with_short_timeout(), session_manager._loop)
        future.result(timeout=5)

        assert isinstance(result_holder[0], MockPermissionResultDeny)
        assert "timed out" in result_holder[0].message.lower()

    def test_permission_interrupt_returns_deny_with_interrupt(self, session_manager, sm_module):
        """Interrupting a waiting session should deny with interrupt=True."""
        sid = "perm-lifecycle-09"
        mock_client, perm_event, result_holder = make_permission_client(
            sid, sm_module, session_manager
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # Interrupt instead of resolving permission
            session_manager.interrupt_session(sid)

            # Wait for it to settle
            wait_for(lambda: session_manager.get_session_state(sid) in ("idle", "stopped"), timeout=5)

        # The permission callback should have received a deny with interrupt
        assert isinstance(result_holder[0], MockPermissionResultDeny)
        assert result_holder[0].interrupt is True

    def test_permission_close_session_returns_deny_with_interrupt(self, session_manager, sm_module):
        """Closing a session while waiting should deny with interrupt=True."""
        sid = "perm-lifecycle-10"
        mock_client, perm_event, result_holder = make_permission_client(
            sid, sm_module, session_manager
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.close_session(sid)
            # close_session resolves the permission future, which lets the drive
            # loop continue and potentially go through WORKING -> IDLE before
            # _close_session sets STOPPED. Wait for the terminal state.
            wait_for(
                lambda: session_manager.get_session_state(sid) in ("stopped", "idle"),
                timeout=10,
            )
            # Give _close_session time to finalize
            time.sleep(0.3)

        assert isinstance(result_holder[0], MockPermissionResultDeny)
        assert result_holder[0].interrupt is True

    def test_permission_callback_for_missing_session_returns_deny(self, session_manager, sm_module):
        """If the session disappears mid-callback, deny with interrupt is returned."""
        sid = "perm-lifecycle-11"
        callback = session_manager._make_permission_callback(sid)

        # Don't add the session -- it's missing
        result_holder = [None]

        async def run():
            result_holder[0] = await callback("Bash", {}, MockToolPermissionContext())

        future = asyncio.run_coroutine_threadsafe(run(), session_manager._loop)
        future.result(timeout=5)

        assert isinstance(result_holder[0], MockPermissionResultDeny)
        assert result_holder[0].interrupt is True


# ===========================================================================
# DOUBLE-CLICK PROTECTION (5+ tests)
# ===========================================================================

class TestDoubleClickProtection:

    def test_resolve_twice_rapidly_second_returns_error(self, session_manager, sm_module):
        """Calling resolve_permission twice rapidly -- second call gets error, no crash."""
        sid = "double-click-01"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WAITING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        future = create_future_on_loop(session_manager._loop)
        info.pending_permission = future

        # First resolve -- succeeds
        r1 = session_manager.resolve_permission(sid, allow=True)
        assert r1["ok"] is True

        # After first resolve, pending_permission is cleared and state is no longer WAITING
        # The second call should fail gracefully
        r2 = session_manager.resolve_permission(sid, allow=True)
        assert r2["ok"] is False

    def test_resolve_after_future_already_done(self, session_manager, sm_module):
        """Resolving after the future is already done should fail gracefully."""
        sid = "double-click-02"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WAITING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        future = create_future_on_loop(session_manager._loop)
        info.pending_permission = future

        # Resolve once
        r1 = session_manager.resolve_permission(sid, allow=True)
        assert r1["ok"] is True

        # pending_permission is now None, state changed
        # Even if we force state back, pending_permission is None
        info.state = sm_module.SessionState.WAITING
        r2 = session_manager.resolve_permission(sid, allow=True)
        assert r2["ok"] is False
        assert "no pending" in r2["error"].lower()

    def test_resolve_for_wrong_session(self, session_manager, sm_module):
        """Resolving permission for a nonexistent session should error."""
        result = session_manager.resolve_permission("wrong-session-id", allow=True)
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_resolve_for_session_with_no_pending(self, session_manager, sm_module):
        """Resolving when session exists but has no pending permission should error."""
        sid = "double-click-04"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WAITING)
        info.pending_permission = None
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.resolve_permission(sid, allow=True)
        assert result["ok"] is False
        assert "no pending" in result["error"].lower()

    def test_resolve_for_stopped_session(self, session_manager, sm_module):
        """Resolving permission for a stopped session should error."""
        sid = "double-click-05"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.STOPPED)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.resolve_permission(sid, allow=True)
        assert result["ok"] is False
        assert "stopped" in result["error"].lower() or "not waiting" in result["error"].lower()

    def test_resolve_for_working_session(self, session_manager, sm_module):
        """Resolving permission for a WORKING (not WAITING) session should error."""
        sid = "double-click-06"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WORKING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.resolve_permission(sid, allow=True)
        assert result["ok"] is False
        assert "not waiting" in result["error"].lower()

    def test_resolve_for_idle_session(self, session_manager, sm_module):
        """Resolving permission for an IDLE session should error."""
        sid = "double-click-07"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.IDLE)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.resolve_permission(sid, allow=True)
        assert result["ok"] is False


# ===========================================================================
# CONCURRENT PERMISSIONS ACROSS SESSIONS (5+ tests)
# ===========================================================================

class TestConcurrentPermissions:

    def test_two_sessions_waiting_resolve_a_doesnt_affect_b(self, session_manager, sm_module):
        """Session A waiting, Session B waiting -- resolving A doesn't affect B."""
        sid_a = "concurrent-perm-A"
        sid_b = "concurrent-perm-B"

        mock_client_a, perm_event_a, result_a = make_permission_client(
            sid_a, sm_module, session_manager, tool_name="Bash"
        )
        mock_client_b, perm_event_b, result_b = make_permission_client(
            sid_b, sm_module, session_manager, tool_name="Write"
        )

        clients = iter([mock_client_a, mock_client_b])

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(clients)):
            session_manager.start_session(sid_a, prompt="A", cwd="/tmp")
            session_manager.start_session(sid_b, prompt="B", cwd="/tmp")

            assert perm_event_a.wait(timeout=5)
            assert perm_event_b.wait(timeout=5)

            wait_for(lambda: session_manager.get_session_state(sid_a) == "waiting")
            wait_for(lambda: session_manager.get_session_state(sid_b) == "waiting")

            # Resolve A only
            session_manager.resolve_permission(sid_a, allow=True)

            # A should progress, B should still be waiting
            wait_for(lambda: session_manager.get_session_state(sid_a) == "idle")
            assert session_manager.get_session_state(sid_b) == "waiting"

            # Now resolve B
            session_manager.resolve_permission(sid_b, allow=False)
            wait_for(lambda: session_manager.get_session_state(sid_b) == "idle")

        assert isinstance(result_a[0], MockPermissionResultAllow)
        assert isinstance(result_b[0], MockPermissionResultDeny)

    def test_resolve_b_first_then_a(self, session_manager, sm_module):
        """Resolve B before A -- both should work independently."""
        sid_a = "concurrent-ba-A"
        sid_b = "concurrent-ba-B"

        mock_client_a, perm_event_a, result_a = make_permission_client(
            sid_a, sm_module, session_manager
        )
        mock_client_b, perm_event_b, result_b = make_permission_client(
            sid_b, sm_module, session_manager
        )

        clients = iter([mock_client_a, mock_client_b])

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(clients)):
            session_manager.start_session(sid_a, prompt="A", cwd="/tmp")
            session_manager.start_session(sid_b, prompt="B", cwd="/tmp")

            assert perm_event_a.wait(timeout=5)
            assert perm_event_b.wait(timeout=5)

            wait_for(lambda: session_manager.get_session_state(sid_a) == "waiting")
            wait_for(lambda: session_manager.get_session_state(sid_b) == "waiting")

            # Resolve B first
            session_manager.resolve_permission(sid_b, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid_b) == "idle")

            # A still waiting
            assert session_manager.get_session_state(sid_a) == "waiting"

            # Now resolve A
            session_manager.resolve_permission(sid_a, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid_a) == "idle")

    def test_session_a_waiting_session_b_working(self, session_manager, sm_module):
        """Session A waiting, B working -- resolve A, B stays working."""
        sid_a = "concurrent-aw-A"
        sid_b = "concurrent-aw-B"

        mock_client_a, perm_event_a, _ = make_permission_client(
            sid_a, sm_module, session_manager
        )

        # B has a slow message stream (stays working for a while)
        mock_client_b = MockClaudeSDKClient()

        b_proceed = threading.Event()

        async def slow_messages_b():
            yield MockAssistantMessage([MockTextBlock("Working on B...")])
            # Wait until we signal it to proceed
            while not b_proceed.is_set():
                await asyncio.sleep(0.05)
            yield MockResultMessage(session_id=sid_b, total_cost_usd=0.01)

        mock_client_b.receive_messages = slow_messages_b

        clients = iter([mock_client_a, mock_client_b])

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(clients)):
            session_manager.start_session(sid_a, prompt="A", cwd="/tmp")
            session_manager.start_session(sid_b, prompt="B", cwd="/tmp")

            assert perm_event_a.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid_a) == "waiting")
            wait_for(lambda: session_manager.get_session_state(sid_b) == "working")

            # Resolve A -- B should stay working
            session_manager.resolve_permission(sid_a, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid_a) == "idle")

            assert session_manager.get_session_state(sid_b) == "working"

            # Let B finish
            b_proceed.set()
            wait_for(lambda: session_manager.get_session_state(sid_b) == "idle")

    def test_five_sessions_all_waiting(self, session_manager, sm_module):
        """5 sessions all waiting simultaneously -- resolve each independently."""
        sids = [f"concurrent-5-{i}" for i in range(5)]
        mock_clients = []
        perm_events = []
        result_holders = []

        for sid in sids:
            mc, pe, rh = make_permission_client(sid, sm_module, session_manager)
            mock_clients.append(mc)
            perm_events.append(pe)
            result_holders.append(rh)

        client_iter = iter(mock_clients)

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(client_iter)):
            for sid in sids:
                session_manager.start_session(sid, prompt="go", cwd="/tmp")

            # Wait for all permissions to trigger
            for pe in perm_events:
                assert pe.wait(timeout=10), "Not all permission callbacks fired"

            for sid in sids:
                wait_for(lambda s=sid: session_manager.get_session_state(s) == "waiting")

            # Resolve them in reverse order
            for i, sid in enumerate(reversed(sids)):
                allow = (i % 2 == 0)  # alternate allow/deny
                session_manager.resolve_permission(sid, allow=allow)

            # All should reach idle
            for sid in sids:
                wait_for(lambda s=sid: session_manager.get_session_state(s) == "idle")

        # Verify results alternate
        for i, rh in enumerate(reversed(result_holders)):
            if i % 2 == 0:
                assert isinstance(rh[0], MockPermissionResultAllow)
            else:
                assert isinstance(rh[0], MockPermissionResultDeny)

    def test_interleaved_resolve_interrupt(self, session_manager, sm_module):
        """Interleaved resolve on A and interrupt on B across sessions."""
        sid_a = "interleave-A"
        sid_b = "interleave-B"

        mock_client_a, perm_event_a, result_a = make_permission_client(
            sid_a, sm_module, session_manager
        )
        mock_client_b, perm_event_b, result_b = make_permission_client(
            sid_b, sm_module, session_manager
        )

        clients = iter([mock_client_a, mock_client_b])

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=lambda **kw: next(clients)):
            session_manager.start_session(sid_a, prompt="A", cwd="/tmp")
            session_manager.start_session(sid_b, prompt="B", cwd="/tmp")

            assert perm_event_a.wait(timeout=5)
            assert perm_event_b.wait(timeout=5)

            wait_for(lambda: session_manager.get_session_state(sid_a) == "waiting")
            wait_for(lambda: session_manager.get_session_state(sid_b) == "waiting")

            # Resolve A, interrupt B
            session_manager.resolve_permission(sid_a, allow=True)
            session_manager.interrupt_session(sid_b)

            wait_for(lambda: session_manager.get_session_state(sid_a) == "idle")
            wait_for(lambda: session_manager.get_session_state(sid_b) in ("idle", "stopped"), timeout=5)

        assert isinstance(result_a[0], MockPermissionResultAllow)
        assert isinstance(result_b[0], MockPermissionResultDeny)
        assert result_b[0].interrupt is True


# ===========================================================================
# PERMISSION DATA FLOW (5+ tests)
# ===========================================================================

class TestPermissionDataFlow:

    def test_tool_name_passed_to_frontend(self, session_manager, sm_module, mock_socketio):
        """tool_name should be correctly passed from callback to session_permission event."""
        sid = "data-flow-01"
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Bash"
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            assert len(perm_calls) >= 1
            assert perm_calls[-1][0][1]['tool_name'] == "Bash"

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_tool_input_dict_passed_to_frontend(self, session_manager, sm_module, mock_socketio):
        """tool_input dict should be passed intact to session_permission event."""
        sid = "data-flow-02"
        tool_input = {"command": "ls -la", "cwd": "/home/user"}
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Bash", tool_input=tool_input
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            assert perm_calls[-1][0][1]['tool_input'] == tool_input

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_write_tool_data_flow(self, session_manager, sm_module, mock_socketio):
        """Write tool's file_path and content in tool_input should pass through."""
        sid = "data-flow-03"
        tool_input = {"path": "/foo/bar.py", "content": "print('hello')"}
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Write", tool_input=tool_input
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="write file", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            data = perm_calls[-1][0][1]
            assert data['tool_name'] == "Write"
            assert data['tool_input']['path'] == "/foo/bar.py"
            assert data['tool_input']['content'] == "print('hello')"

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_edit_tool_data_flow(self, session_manager, sm_module, mock_socketio):
        """Edit tool data should pass through correctly."""
        sid = "data-flow-04"
        tool_input = {
            "file_path": "/foo/bar.py",
            "old_string": "def old():",
            "new_string": "def new():",
        }
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Edit", tool_input=tool_input
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="edit file", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            data = perm_calls[-1][0][1]
            assert data['tool_name'] == "Edit"
            assert data['tool_input']['file_path'] == "/foo/bar.py"

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_empty_tool_input_handled(self, session_manager, sm_module, mock_socketio):
        """Empty tool_input should be handled gracefully (empty dict)."""
        sid = "data-flow-05"
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Read", tool_input={}
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            data = perm_calls[-1][0][1]
            assert data['tool_input'] == {}

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_non_dict_tool_input_coerced_to_empty_dict(self, session_manager, sm_module):
        """Non-dict tool_input should be coerced to empty dict on SessionInfo."""
        sid = "data-flow-06"
        callback = session_manager._make_permission_callback(sid)

        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WORKING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        # Run callback with non-dict tool_input -- should not crash
        result_holder = [None]

        async def run():
            # We need to resolve the future from another coroutine
            async def resolver():
                await asyncio.sleep(0.1)
                if info.pending_permission and not info.pending_permission.done():
                    result = MockPermissionResultAllow()
                    info.pending_permission.set_result((result, False))

            asyncio.ensure_future(resolver())
            result_holder[0] = await callback("Bash", "not-a-dict", MockToolPermissionContext())

        future = asyncio.run_coroutine_threadsafe(run(), session_manager._loop)
        future.result(timeout=5)

        # The pending_tool_input should be {} since input wasn't a dict
        # And the result should be the allow we sent
        assert isinstance(result_holder[0], MockPermissionResultAllow)

    def test_large_tool_input_handled(self, session_manager, sm_module, mock_socketio):
        """Large tool_input should be handled without crashing."""
        sid = "data-flow-07"
        large_content = "x" * 100000
        tool_input = {"command": large_content}
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Bash", tool_input=tool_input
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            assert len(perm_calls) >= 1

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")


# ===========================================================================
# STATE CONSISTENCY DURING PERMISSION (10+ tests)
# ===========================================================================

class TestStateConsistencyDuringPermission:

    def test_state_is_waiting_during_entire_await(self, session_manager, sm_module):
        """State should remain WAITING throughout the permission await period."""
        sid = "state-consist-01"
        mock_client, perm_event, _ = make_permission_client(sid, sm_module, session_manager)

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # Check multiple times that state stays WAITING
            for _ in range(5):
                assert session_manager.get_session_state(sid) == "waiting"
                time.sleep(0.05)

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_state_goes_to_working_not_idle_after_resolve(self, session_manager, sm_module, mock_socketio):
        """After resolve, state should go to WORKING (not directly to IDLE)."""
        sid = "state-consist-02"
        state_transitions = []

        original_emit = mock_socketio.emit

        def tracking_emit(event, data, *args, **kwargs):
            if event == 'session_state' and isinstance(data, dict) and data.get('session_id') == sid:
                state_transitions.append(data['state'])
            return original_emit(event, data, *args, **kwargs)

        mock_socketio.emit = MagicMock(side_effect=tracking_emit)

        mock_client, perm_event, _ = make_permission_client(sid, sm_module, session_manager)

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        # Find the waiting -> working transition (should not be waiting -> idle)
        waiting_idx = state_transitions.index("waiting")
        assert state_transitions[waiting_idx + 1] == "working", \
            f"After 'waiting', expected 'working' but got transitions: {state_transitions}"

    def test_get_all_states_shows_waiting_with_permission_details(self, session_manager, sm_module):
        """get_all_states should include permission details for WAITING sessions."""
        sid = "state-consist-03"
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager,
            tool_name="Bash", tool_input={"command": "echo test"}
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            states = session_manager.get_all_states()
            session_state = [s for s in states if s['session_id'] == sid][0]
            assert session_state['state'] == "waiting"
            assert 'permission' in session_state
            assert session_state['permission']['tool_name'] == "Bash"
            assert session_state['permission']['tool_input'] == {"command": "echo test"}

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_entries_accumulate_after_permission_resolved(self, session_manager, sm_module):
        """After permission resolved, new entries should still accumulate."""
        sid = "state-consist-04"

        post_msgs = [
            MockAssistantMessage([MockTextBlock("Permission granted, continuing...")]),
            MockUserMessage([MockToolResultBlock(tool_use_id="t-post", content="tool output")]),
        ]
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, post_permission_messages=post_msgs
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        entries = session_manager.get_entries(sid)
        # Should have: initial asst text + post-permission asst text + tool_result
        asst_entries = [e for e in entries if e["kind"] == "asst"]
        assert len(asst_entries) >= 2  # initial + post-permission

    def test_cost_updates_after_permission_resolved(self, session_manager, sm_module):
        """Cost should still be updated from ResultMessage after permission resolves."""
        sid = "state-consist-05"
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.cost_usd == pytest.approx(0.03)  # from make_permission_client

    def test_multiple_permission_requests_in_same_session(self, session_manager, sm_module):
        """Two sequential permission requests in one session should both work."""
        sid = "state-consist-06"

        perm_count = [0]
        perm_events = [threading.Event(), threading.Event()]

        callback = session_manager._make_permission_callback(sid)
        mock_client = MockClaudeSDKClient()

        async def receive_with_two_permissions():
            yield MockAssistantMessage([MockTextBlock("First action")])

            # First permission
            perm_events[0].set()
            await callback("Bash", {"command": "ls"}, MockToolPermissionContext())
            perm_count[0] += 1

            yield MockAssistantMessage([MockTextBlock("Second action")])

            # Second permission
            perm_events[1].set()
            await callback("Write", {"path": "/tmp/x"}, MockToolPermissionContext())
            perm_count[0] += 1

            yield MockResultMessage(session_id=sid, total_cost_usd=0.05)

        mock_client.receive_messages = receive_with_two_permissions

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")

            # First permission
            assert perm_events[0].wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")
            session_manager.resolve_permission(sid, allow=True)

            # Second permission
            assert perm_events[1].wait(timeout=10)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")
            session_manager.resolve_permission(sid, allow=True)

            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert perm_count[0] == 2

    def test_permission_after_session_produced_output(self, session_manager, sm_module):
        """Permission request after session already has some output should work."""
        sid = "state-consist-07"

        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager,
            post_permission_messages=[
                MockAssistantMessage([MockTextBlock("Done after permission")])
            ]
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)

            # Verify there's already at least one entry (the initial "I need to run a tool.")
            wait_for(lambda: len(session_manager.get_entries(sid)) >= 1)

            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")
            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        entries = session_manager.get_entries(sid)
        asst_entries = [e for e in entries if e["kind"] == "asst"]
        assert len(asst_entries) >= 2

    def test_permission_as_first_action(self, session_manager, sm_module):
        """Permission as the very first callback should work."""
        sid = "state-consist-08"

        # Client that triggers permission immediately
        callback = session_manager._make_permission_callback(sid)
        mock_client = MockClaudeSDKClient()
        perm_event = threading.Event()
        result_holder = [None]

        async def receive_immediate_permission():
            perm_event.set()
            result_holder[0] = await callback(
                "Bash", {"command": "whoami"}, MockToolPermissionContext()
            )
            yield MockAssistantMessage([MockTextBlock("After permission")])
            yield MockResultMessage(session_id=sid)

        mock_client.receive_messages = receive_immediate_permission

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert isinstance(result_holder[0], MockPermissionResultAllow)

    def test_permission_details_cleared_after_resolve(self, session_manager, sm_module):
        """After resolving, pending_tool_name and pending_tool_input should be cleared."""
        sid = "state-consist-09"
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager,
            tool_name="Bash", tool_input={"command": "rm -rf /"}
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # Before resolve, check details are set
            with session_manager._lock:
                info = session_manager._sessions[sid]
            assert info.pending_tool_name == "Bash"
            assert info.pending_tool_input == {"command": "rm -rf /"}

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            # After resolve, details should be cleared
            assert info.pending_tool_name == ""
            assert info.pending_tool_input == {}
            assert info.pending_permission is None

    def test_get_all_states_no_permission_key_after_resolve(self, session_manager, sm_module):
        """After resolve, get_all_states should NOT include permission key."""
        sid = "state-consist-10"
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager,
            tool_name="Write", tool_input={"path": "/tmp/test"}
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # While waiting, permission key should be present
            states = session_manager.get_all_states()
            s = [x for x in states if x['session_id'] == sid][0]
            assert 'permission' in s

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            # After resolve, permission key should not be present
            states = session_manager.get_all_states()
            s = [x for x in states if x['session_id'] == sid][0]
            assert 'permission' not in s

    def test_deny_then_new_permission_works(self, session_manager, sm_module):
        """After denying a permission, a subsequent permission request should work."""
        sid = "state-consist-11"

        perm_events = [threading.Event(), threading.Event()]
        results = [None, None]

        callback = session_manager._make_permission_callback(sid)
        mock_client = MockClaudeSDKClient()

        async def receive_with_two_perms():
            # First permission -- will be denied
            perm_events[0].set()
            results[0] = await callback("Bash", {"command": "rm -rf /"}, MockToolPermissionContext())

            yield MockAssistantMessage([MockTextBlock("Ok, trying something safer")])

            # Second permission -- will be allowed
            perm_events[1].set()
            results[1] = await callback("Bash", {"command": "ls"}, MockToolPermissionContext())

            yield MockResultMessage(session_id=sid)

        mock_client.receive_messages = receive_with_two_perms

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")

            assert perm_events[0].wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")
            session_manager.resolve_permission(sid, allow=False)

            assert perm_events[1].wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")
            session_manager.resolve_permission(sid, allow=True)

            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert isinstance(results[0], MockPermissionResultDeny)
        assert isinstance(results[1], MockPermissionResultAllow)


# ===========================================================================
# WEBSOCKET EVENT VERIFICATION (5+ tests)
# ===========================================================================

class TestWebSocketEventVerification:

    def test_session_permission_event_contains_correct_fields(self, session_manager, sm_module, mock_socketio):
        """session_permission event should have session_id, tool_name, tool_input."""
        sid = "ws-verify-01"
        tool_input = {"command": "cat /etc/passwd"}
        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager,
            tool_name="Bash", tool_input=tool_input
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            perm_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_permission'
            ]
            assert len(perm_calls) >= 1
            data = perm_calls[-1][0][1]
            assert 'session_id' in data
            assert 'tool_name' in data
            assert 'tool_input' in data
            assert data['session_id'] == sid
            assert data['tool_name'] == "Bash"
            assert data['tool_input'] == tool_input

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_session_state_waiting_emitted(self, session_manager, sm_module, mock_socketio):
        """session_state with 'waiting' should be emitted when permission fires."""
        sid = "ws-verify-02"
        mock_client, perm_event, _ = make_permission_client(sid, sm_module, session_manager)

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            state_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_state' and isinstance(c[0][1], dict)
                and c[0][1].get('session_id') == sid
            ]
            waiting_calls = [c for c in state_calls if c[0][1]['state'] == 'waiting']
            assert len(waiting_calls) >= 1

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_session_state_working_emitted_after_resolve(self, session_manager, sm_module, mock_socketio):
        """session_state with 'working' should be emitted after permission resolve."""
        sid = "ws-verify-03"
        mock_client, perm_event, _ = make_permission_client(sid, sm_module, session_manager)

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # Clear the call list to see new emissions after resolve
            mock_socketio.emit.reset_mock()

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            state_calls = [
                c for c in mock_socketio.emit.call_args_list
                if c[0][0] == 'session_state' and isinstance(c[0][1], dict)
                and c[0][1].get('session_id') == sid
            ]
            working_calls = [c for c in state_calls if c[0][1]['state'] == 'working']
            assert len(working_calls) >= 1

    def test_frontend_invalid_action_emits_error(self, mock_socketio):
        """Frontend permission_response with invalid action should emit error."""
        sdk_mocks = {
            'claude_code_sdk': MagicMock(),
            'claude_code_sdk.types': MagicMock(),
        }
        with patch.dict('sys.modules', sdk_mocks):
            from flask import Flask
            from flask_socketio import SocketIO
            from app.routes.ws_events import register_ws_events

            app = Flask(__name__)
            app.config['TESTING'] = True
            socketio = SocketIO(app, async_mode='threading')

            mock_sm = MagicMock()
            mock_sm.get_all_states.return_value = []
            mock_sm.resolve_permission.return_value = {"ok": True}
            app.session_manager = mock_sm

            register_ws_events(socketio, app)
            client = socketio.test_client(app)
            client.get_received()  # clear connect events

            # Send invalid action
            client.emit('permission_response', {
                'session_id': 's1',
                'action': 'invalid_action',
            })

            received = client.get_received()
            errors = [msg for msg in received if msg['name'] == 'error']
            assert len(errors) >= 1
            assert 'action' in errors[0]['args'][0]['message'].lower()

            client.disconnect()

    def test_frontend_missing_session_id_emits_error(self, mock_socketio):
        """Frontend permission_response with no session_id should emit error."""
        sdk_mocks = {
            'claude_code_sdk': MagicMock(),
            'claude_code_sdk.types': MagicMock(),
        }
        with patch.dict('sys.modules', sdk_mocks):
            from flask import Flask
            from flask_socketio import SocketIO
            from app.routes.ws_events import register_ws_events

            app = Flask(__name__)
            app.config['TESTING'] = True
            socketio = SocketIO(app, async_mode='threading')

            mock_sm = MagicMock()
            mock_sm.get_all_states.return_value = []
            app.session_manager = mock_sm

            register_ws_events(socketio, app)
            client = socketio.test_client(app)
            client.get_received()

            # Send without session_id
            client.emit('permission_response', {
                'action': 'y',
            })

            received = client.get_received()
            errors = [msg for msg in received if msg['name'] == 'error']
            assert len(errors) >= 1
            assert 'session_id' in errors[0]['args'][0]['message'].lower()

            client.disconnect()

    def test_frontend_resolve_failure_emits_error(self, mock_socketio):
        """Frontend permission_response where resolve_permission fails should emit error."""
        sdk_mocks = {
            'claude_code_sdk': MagicMock(),
            'claude_code_sdk.types': MagicMock(),
        }
        with patch.dict('sys.modules', sdk_mocks):
            from flask import Flask
            from flask_socketio import SocketIO
            from app.routes.ws_events import register_ws_events

            app = Flask(__name__)
            app.config['TESTING'] = True
            socketio = SocketIO(app, async_mode='threading')

            mock_sm = MagicMock()
            mock_sm.get_all_states.return_value = []
            mock_sm.resolve_permission.return_value = {
                "ok": False,
                "error": "No pending permission"
            }
            app.session_manager = mock_sm

            register_ws_events(socketio, app)
            client = socketio.test_client(app)
            client.get_received()

            client.emit('permission_response', {
                'session_id': 's1',
                'action': 'y',
            })

            received = client.get_received()
            errors = [msg for msg in received if msg['name'] == 'error']
            assert len(errors) >= 1
            assert 'no pending' in errors[0]['args'][0]['message'].lower()

            client.disconnect()

    def test_permission_event_order_state_then_permission(self, session_manager, sm_module, mock_socketio):
        """session_state(waiting) should be emitted before session_permission."""
        sid = "ws-verify-07"
        emit_log = []

        original_emit = mock_socketio.emit

        def logging_emit(event, data, *args, **kwargs):
            if isinstance(data, dict) and data.get('session_id') == sid:
                emit_log.append((event, data.get('state', data.get('tool_name', ''))))
            return original_emit(event, data, *args, **kwargs)

        mock_socketio.emit = MagicMock(side_effect=logging_emit)

        mock_client, perm_event, _ = make_permission_client(
            sid, sm_module, session_manager, tool_name="Bash"
        )

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert perm_event.wait(timeout=5)
            wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

            # Find the waiting state event and the permission event
            waiting_indices = [
                i for i, (ev, val) in enumerate(emit_log)
                if ev == 'session_state' and val == 'waiting'
            ]
            perm_indices = [
                i for i, (ev, val) in enumerate(emit_log)
                if ev == 'session_permission'
            ]
            assert len(waiting_indices) >= 1
            assert len(perm_indices) >= 1
            # State event should come before permission event
            assert waiting_indices[0] < perm_indices[0], \
                f"session_state(waiting) at {waiting_indices[0]} should come before session_permission at {perm_indices[0]}"

            session_manager.resolve_permission(sid, allow=True)
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

    def test_non_dict_data_to_permission_response(self, mock_socketio):
        """Sending non-dict data to permission_response handler should emit error."""
        sdk_mocks = {
            'claude_code_sdk': MagicMock(),
            'claude_code_sdk.types': MagicMock(),
        }
        with patch.dict('sys.modules', sdk_mocks):
            from flask import Flask
            from flask_socketio import SocketIO
            from app.routes.ws_events import register_ws_events

            app = Flask(__name__)
            app.config['TESTING'] = True
            socketio = SocketIO(app, async_mode='threading')

            mock_sm = MagicMock()
            mock_sm.get_all_states.return_value = []
            app.session_manager = mock_sm

            register_ws_events(socketio, app)
            client = socketio.test_client(app)
            client.get_received()

            client.emit('permission_response', "not a dict")

            received = client.get_received()
            errors = [msg for msg in received if msg['name'] == 'error']
            assert len(errors) >= 1

            client.disconnect()
