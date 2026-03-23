"""
Comprehensive state-transition tests for SessionManager.

Covers every edge of the state machine:
    STARTING -> WORKING -> IDLE -> (send_message) -> WORKING -> ...
    STARTING -> WORKING -> WAITING -> (resolve) -> WORKING -> ...
    Any -> STOPPED  (on error, close, or cancel)
    STOPPED -> STARTING  (on restart)

Replaces the old test_state_sync.py and test_full_stack_failures.py.
"""

import asyncio
import threading
import time
import pytest
from unittest.mock import MagicMock, patch
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

    async def get_server_info(self):
        return {"version": "mock"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_socketio():
    sio = MagicMock()
    sio.emit = MagicMock()
    return sio


@pytest.fixture
def mock_sdk_types():
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


def make_future_on_loop(loop):
    """Create an asyncio.Future on the given loop from an external thread."""
    created = [None]
    event = threading.Event()

    def _create():
        created[0] = loop.create_future()
        event.set()

    loop.call_soon_threadsafe(_create)
    event.wait(timeout=5)
    return created[0]


def collect_emitted_states(mock_socketio, session_id):
    """Return all session_state payloads emitted for a given session_id."""
    results = []
    for call in mock_socketio.emit.call_args_list:
        args = call[0]
        if len(args) >= 2 and args[0] == 'session_state':
            payload = args[1]
            if payload.get('session_id') == session_id:
                results.append(payload)
    return results


# ===================================================================
# HAPPY PATH TRANSITIONS (10+ tests)
# ===================================================================

class TestHappyPathTransitions:

    def test_starting_to_working_on_connect(self, session_manager, sm_module):
        """STARTING -> WORKING on successful connect."""
        sid = "hp-start-work"
        states_seen = []

        original_emit = session_manager._emit_state

        def tracking_emit(info):
            states_seen.append(info.state.value)
            original_emit(info)

        session_manager._emit_state = tracking_emit

        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("Hello")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="Hi", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        # Must have gone through starting -> working
        assert "starting" in states_seen
        assert "working" in states_seen

    def test_working_to_idle_on_result_message(self, session_manager, sm_module):
        """WORKING -> IDLE when ResultMessage is received."""
        sid = "hp-work-idle"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("Done")]),
            MockResultMessage(session_id=sid, total_cost_usd=0.01),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="test", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert session_manager.get_session_state(sid) == "idle"

    def test_idle_to_working_on_send_message(self, session_manager, sm_module):
        """IDLE -> WORKING on send_message."""
        sid = "hp-idle-work"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockResultMessage(session_id=sid),
        ]
        mock_client._response_messages = [
            MockAssistantMessage([MockTextBlock("Reply")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="init", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            # Capture states after sending
            states_after_send = []
            original_emit = session_manager._emit_state

            def track(info):
                if info.session_id == sid:
                    states_after_send.append(info.state.value)
                original_emit(info)

            session_manager._emit_state = track

            result = session_manager.send_message(sid, "Follow-up")
            assert result["ok"] is True

            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert "working" in states_after_send

    def test_working_to_waiting_on_permission(self, session_manager, sm_module):
        """WORKING -> WAITING when permission callback fires."""
        sid = "hp-work-wait"

        # We need to intercept the permission callback so the session
        # actually pauses in WAITING state.
        permission_reached = threading.Event()
        release_permission = threading.Event()

        mock_client = MockClaudeSDKClient()

        async def slow_connect(prompt=None):
            mock_client._connected = True
            mock_client.connect_prompt = prompt

        mock_client.connect = slow_connect

        async def messages_with_permission():
            # Yield one message, then nothing -- the permission callback
            # is triggered by the SDK options, not by messages.
            yield MockAssistantMessage([MockTextBlock("Working...")])
            yield MockResultMessage(session_id=sid)

        mock_client.receive_messages = messages_with_permission

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="test", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        # For the direct permission test, manually set up the session in
        # WORKING state and invoke the callback.
        with session_manager._lock:
            info = session_manager._sessions[sid]
        info.state = sm_module.SessionState.WORKING

        # Build the permission callback
        callback = session_manager._make_permission_callback(sid)

        # Run the callback on the event loop -- it should set state to WAITING
        async def run_callback():
            # Run in a separate task so we can check state before it resolves
            task = asyncio.ensure_future(
                callback("Bash", {"command": "rm -rf /"}, MockToolPermissionContext())
            )
            # Give the callback time to set WAITING
            await asyncio.sleep(0.1)
            return task

        future = asyncio.run_coroutine_threadsafe(run_callback(), session_manager._loop)
        task = future.result(timeout=5)

        wait_for(lambda: session_manager.get_session_state(sid) == "waiting")
        assert session_manager.get_session_state(sid) == "waiting"

        # Now resolve permission to let it continue
        session_manager.resolve_permission(sid, allow=True)
        wait_for(lambda: session_manager.get_session_state(sid) == "working")

    def test_waiting_to_working_on_permission_resolve(self, session_manager, sm_module):
        """WAITING -> WORKING after resolve_permission(allow=True)."""
        sid = "hp-wait-work"
        info = sm_module.SessionInfo(session_id=sid, state=sm_module.SessionState.WORKING)
        with session_manager._lock:
            session_manager._sessions[sid] = info

        callback = session_manager._make_permission_callback(sid)

        # Fire callback in background -- it will set state to WAITING
        async def run_and_wait():
            task = asyncio.ensure_future(
                callback("Write", {"path": "/x"}, MockToolPermissionContext())
            )
            await asyncio.sleep(0.1)
            return task

        f = asyncio.run_coroutine_threadsafe(run_and_wait(), session_manager._loop)
        task = f.result(timeout=5)

        wait_for(lambda: session_manager.get_session_state(sid) == "waiting")

        # Resolve
        res = session_manager.resolve_permission(sid, allow=True)
        assert res["ok"] is True

        wait_for(lambda: session_manager.get_session_state(sid) == "working")
        assert session_manager.get_session_state(sid) == "working"

    def test_idle_to_stopped_on_close(self, session_manager, sm_module):
        """IDLE -> STOPPED on close_session."""
        sid = "hp-idle-stop"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [MockResultMessage(session_id=sid)]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            session_manager.close_session(sid)
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        assert session_manager.get_session_state(sid) == "stopped"

    def test_working_to_stopped_on_close(self, session_manager, sm_module):
        """WORKING -> STOPPED when close_session is called during work."""
        sid = "hp-working-stop"
        mock_client = MockClaudeSDKClient()

        async def slow_messages():
            yield MockAssistantMessage([MockTextBlock("Thinking...")])
            await asyncio.sleep(30)
            yield MockResultMessage(session_id=sid)

        mock_client.receive_messages = slow_messages

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="work", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "working")

            session_manager.close_session(sid)
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        assert session_manager.get_session_state(sid) == "stopped"

    def test_waiting_to_stopped_on_close(self, session_manager, sm_module):
        """WAITING -> STOPPED when close_session is called during permission wait.

        Uses a real _drive_session flow so the permission callback is properly
        embedded in the session driver, and _close_session cancels the driving
        task to avoid the callback waking up and resetting state.
        """
        sid = "hp-waiting-stop"

        permission_reached = threading.Event()

        mock_client = MockClaudeSDKClient()

        # The mock client's connect triggers the can_use_tool callback via
        # the options.  We simulate the permission wait by making
        # receive_messages block until the permission is resolved (which it
        # won't be, because we're going to close the session instead).
        async def blocking_messages():
            yield MockAssistantMessage([MockTextBlock("Working...")])
            # Block for a long time -- close_session should cancel us
            await asyncio.sleep(300)
            yield MockResultMessage(session_id=sid)  # pragma: no cover

        mock_client.receive_messages = blocking_messages

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "working")

        # Manually put the session into WAITING state (simulating a permission
        # request arriving) and set up a pending_permission future.
        with session_manager._lock:
            info = session_manager._sessions[sid]
        info.state = sm_module.SessionState.WAITING
        future = make_future_on_loop(session_manager._loop)
        info.pending_permission = future

        assert session_manager.get_session_state(sid) == "waiting"

        session_manager.close_session(sid)
        wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

    def test_stopped_to_starting_to_working_on_restart(self, session_manager, sm_module):
        """STOPPED -> STARTING -> WORKING -> IDLE on restart."""
        sid = "hp-restart"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.STOPPED
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        states_seen = []
        original_emit = session_manager._emit_state

        def track(info_obj):
            if info_obj.session_id == sid:
                states_seen.append(info_obj.state.value)
            original_emit(info_obj)

        session_manager._emit_state = track

        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("Restarted")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            result = session_manager.start_session(sid, prompt="go", cwd="/tmp")
            assert result["ok"] is True
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert "starting" in states_seen
        assert "working" in states_seen
        assert "idle" in states_seen

    def test_full_lifecycle_start_work_idle_send_work_idle_close(
        self, session_manager, sm_module
    ):
        """Full round trip: start -> work -> idle -> send -> work -> idle -> close -> stopped."""
        sid = "hp-full"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("First reply")]),
            MockResultMessage(session_id=sid),
        ]
        mock_client._response_messages = [
            MockAssistantMessage([MockTextBlock("Second reply")]),
            MockResultMessage(session_id=sid, total_cost_usd=0.02),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="hi", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            result = session_manager.send_message(sid, "More")
            assert result["ok"] is True
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            session_manager.close_session(sid)
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        assert session_manager.get_session_state(sid) == "stopped"


# ===================================================================
# ERROR TRANSITIONS (10+ tests)
# ===================================================================

class TestErrorTransitions:

    def test_starting_to_stopped_on_connect_failure(self, session_manager, sm_module):
        """STARTING -> STOPPED when connect() throws."""
        sid = "err-connect"
        mock_client = MockClaudeSDKClient()

        async def bad_connect(prompt=None):
            raise ConnectionError("Cannot reach server")

        mock_client.connect = bad_connect

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.error is not None
        assert "Cannot reach server" in info.error

    def test_working_to_stopped_on_receive_messages_error(
        self, session_manager, sm_module
    ):
        """WORKING -> STOPPED when receive_messages raises."""
        sid = "err-recv"
        mock_client = MockClaudeSDKClient()

        async def broken_messages():
            yield MockAssistantMessage([MockTextBlock("partial")])
            raise RuntimeError("stream broke")

        mock_client.receive_messages = broken_messages

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert "stream broke" in info.error

    def test_working_to_stopped_on_query_error(self, session_manager, sm_module):
        """WORKING -> STOPPED when query() throws during send_message."""
        sid = "err-query"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [MockResultMessage(session_id=sid)]

        async def bad_query(prompt, session_id="default"):
            raise RuntimeError("query failed")

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="init", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            mock_client.query = bad_query
            session_manager.send_message(sid, "boom")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert "query failed" in info.error

    def test_stopped_state_has_error_message(self, session_manager, sm_module):
        """Error field is set when session transitions to STOPPED with exception."""
        sid = "err-msg"
        mock_client = MockClaudeSDKClient()

        async def fail_connect(prompt=None):
            raise ValueError("bad value")

        mock_client.connect = fail_connect

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.error == "bad value"

    def test_error_entry_added_to_log(self, session_manager, sm_module):
        """An error LogEntry with is_error=True is appended on failure."""
        sid = "err-log"
        mock_client = MockClaudeSDKClient()

        async def fail_connect(prompt=None):
            raise RuntimeError("explosion")

        mock_client.connect = fail_connect

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        entries = session_manager.get_entries(sid)
        error_entries = [e for e in entries if e.get("is_error")]
        assert len(error_entries) >= 1
        assert "explosion" in error_entries[0]["text"].lower()

    def test_session_state_event_emitted_with_error(
        self, session_manager, sm_module, mock_socketio
    ):
        """session_state WS event is emitted with error field on failure."""
        sid = "err-emit"
        mock_client = MockClaudeSDKClient()

        async def fail_connect(prompt=None):
            raise RuntimeError("ws-error-test")

        mock_client.connect = fail_connect

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        state_events = collect_emitted_states(mock_socketio, sid)
        stopped_events = [e for e in state_events if e["state"] == "stopped"]
        assert len(stopped_events) >= 1
        assert stopped_events[-1]["error"] is not None

    def test_multiple_errors_dont_crash(self, session_manager, sm_module):
        """Multiple sequential errors should not crash the manager."""
        for i in range(3):
            sid = f"multi-err-{i}"
            mock_client = MockClaudeSDKClient()

            async def fail_connect(prompt=None):
                raise RuntimeError(f"error-{i}")

            mock_client.connect = fail_connect

            with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
                session_manager.start_session(sid, prompt="x", cwd="/tmp")
                wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        # Manager should still be alive
        assert session_manager._started is True

        # Start a successful session to prove it still works
        sid_ok = "multi-err-ok"
        mock_client_ok = MockClaudeSDKClient()
        mock_client_ok._messages = [MockResultMessage(session_id=sid_ok)]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client_ok):
            result = session_manager.start_session(sid_ok, prompt="ok", cwd="/tmp")
            assert result["ok"] is True
            wait_for(lambda: session_manager.get_session_state(sid_ok) == "idle")

    def test_error_during_receive_response(self, session_manager, sm_module):
        """Error during receive_response (send_message path) -> STOPPED."""
        sid = "err-resp"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [MockResultMessage(session_id=sid)]

        async def broken_response():
            raise RuntimeError("response exploded")
            yield  # pragma: no cover -- make it an async generator

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="init", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            mock_client.receive_response = broken_response
            session_manager.send_message(sid, "trigger")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert "response exploded" in info.error

    def test_error_during_permission_callback_results_in_deny(
        self, session_manager, sm_module
    ):
        """If an exception occurs related to a permission flow, session
        should still be recoverable or transition to STOPPED cleanly."""
        sid = "err-perm-cb"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.WORKING
        )
        info.client = MockClaudeSDKClient()
        with session_manager._lock:
            session_manager._sessions[sid] = info

        callback = session_manager._make_permission_callback(sid)

        # Start the permission callback -- it creates a future and waits
        async def run_and_cancel():
            task = asyncio.ensure_future(
                callback("Bash", {"command": "bad"}, MockToolPermissionContext())
            )
            await asyncio.sleep(0.1)
            # Cancel the task to simulate an error path
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return task

        f = asyncio.run_coroutine_threadsafe(run_and_cancel(), session_manager._loop)
        f.result(timeout=5)

        # The session should not be stuck in WAITING after cancellation
        state = session_manager.get_session_state(sid)
        assert state != "waiting"

    def test_connect_timeout_goes_to_stopped(self, session_manager, sm_module):
        """A timeout during connect should result in STOPPED."""
        sid = "err-timeout"
        mock_client = MockClaudeSDKClient()

        async def slow_connect(prompt=None):
            raise TimeoutError("connection timed out")

        mock_client.connect = slow_connect

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert "timed out" in info.error

    def test_sdk_client_constructor_failure(self, session_manager, sm_module):
        """If ClaudeSDKClient() constructor fails, session goes to STOPPED."""
        sid = "err-ctor"

        def broken_ctor(options=None):
            raise RuntimeError("SDK init failed")

        with patch.object(sm_module, 'ClaudeSDKClient', side_effect=broken_ctor):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert "SDK init failed" in info.error


# ===================================================================
# EDGE CASE TRANSITIONS (10+ tests)
# ===================================================================

class TestEdgeCaseTransitions:

    def test_zero_messages_goes_to_idle(self, session_manager, sm_module):
        """receive_messages yields 0 messages -> session goes to IDLE."""
        sid = "edge-zero-msgs"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = []  # No messages at all

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert session_manager.get_session_state(sid) == "idle"

    def test_only_stream_events_still_transitions(self, session_manager, sm_module):
        """receive_messages yields only StreamEvents -> session still reaches IDLE."""
        sid = "edge-streams-only"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockStreamEvent(event="content_block_start", data={"type": "text"}),
            MockStreamEvent(event="content_block_delta", data={"delta": "hi"}),
            MockStreamEvent(event="content_block_stop", data={}),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert session_manager.get_session_state(sid) == "idle"

    def test_result_message_with_is_error(self, session_manager, sm_module):
        """ResultMessage with is_error=True -> IDLE with error set."""
        sid = "edge-result-err"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockResultMessage(session_id=sid, is_error=True),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.error is not None
        assert "error" in info.error.lower()

        entries = session_manager.get_entries(sid)
        error_entries = [e for e in entries if e.get("is_error")]
        assert len(error_entries) >= 1

    def test_result_message_different_session_id(self, session_manager, sm_module):
        """ResultMessage with session_id != ours -> still handled gracefully (IDLE)."""
        sid = "edge-diff-sid"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockResultMessage(session_id="some-other-id", total_cost_usd=0.03),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        assert session_manager.get_session_state(sid) == "idle"
        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.cost_usd == pytest.approx(0.03)

    def test_double_start_returns_error(self, session_manager, sm_module):
        """Starting an already-running session returns error."""
        sid = "edge-double-start"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.WORKING
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.start_session(sid, prompt="x", cwd="/tmp")
        assert result["ok"] is False
        assert "already running" in result["error"].lower()

    def test_send_message_to_working_returns_error(self, session_manager, sm_module):
        """send_message to WORKING session returns error."""
        sid = "edge-send-working"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.WORKING
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.send_message(sid, "text")
        assert result["ok"] is False
        assert "working" in result["error"].lower()

    def test_send_message_to_stopped_returns_error(self, session_manager, sm_module):
        """send_message to STOPPED session returns error."""
        sid = "edge-send-stopped"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.STOPPED
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.send_message(sid, "text")
        assert result["ok"] is False
        assert "stopped" in result["error"].lower()

    def test_send_message_to_starting_returns_error(self, session_manager, sm_module):
        """send_message to STARTING session returns error."""
        sid = "edge-send-starting"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.STARTING
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.send_message(sid, "text")
        assert result["ok"] is False
        assert "starting" in result["error"].lower()

    def test_interrupt_stopped_returns_error(self, session_manager, sm_module):
        """interrupt_session on STOPPED session returns error."""
        sid = "edge-int-stopped"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.STOPPED
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.interrupt_session(sid)
        assert result["ok"] is False
        assert "stopped" in result["error"].lower()

    def test_close_nonexistent_session_returns_error(self, session_manager):
        """close_session on nonexistent session returns error."""
        result = session_manager.close_session("no-such-session")
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_get_entries_nonexistent_returns_empty(self, session_manager):
        """get_entries for unknown session returns empty list."""
        entries = session_manager.get_entries("unknown-session")
        assert entries == []

    def test_get_session_state_nonexistent_returns_none(self, session_manager):
        """get_session_state for unknown session returns None."""
        state = session_manager.get_session_state("unknown-session")
        assert state is None

    def test_send_message_to_waiting_returns_error(self, session_manager, sm_module):
        """send_message to WAITING session returns error."""
        sid = "edge-send-waiting"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.WAITING
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        result = session_manager.send_message(sid, "text")
        assert result["ok"] is False
        assert "waiting" in result["error"].lower()

    def test_interrupt_nonexistent_returns_error(self, session_manager):
        """interrupt_session on nonexistent session returns error."""
        result = session_manager.interrupt_session("ghost-session")
        assert result["ok"] is False
        assert "not found" in result["error"].lower()


# ===================================================================
# STATE CONSISTENCY (10+ tests)
# ===================================================================

class TestStateConsistency:

    def test_get_all_states_reflects_current_state(self, session_manager, sm_module):
        """get_all_states returns the actual current state for every session."""
        with session_manager._lock:
            session_manager._sessions["s1"] = sm_module.SessionInfo(
                session_id="s1", state=sm_module.SessionState.IDLE
            )
            session_manager._sessions["s2"] = sm_module.SessionInfo(
                session_id="s2", state=sm_module.SessionState.WORKING
            )
            session_manager._sessions["s3"] = sm_module.SessionInfo(
                session_id="s3", state=sm_module.SessionState.STOPPED
            )

        states = session_manager.get_all_states()
        by_id = {s["session_id"]: s for s in states}
        assert by_id["s1"]["state"] == "idle"
        assert by_id["s2"]["state"] == "working"
        assert by_id["s3"]["state"] == "stopped"

    def test_get_session_state_matches_at_every_point(
        self, session_manager, sm_module
    ):
        """get_session_state always agrees with internal info.state."""
        sid = "consist-match"
        states_seen = []

        original_emit = session_manager._emit_state

        def track(info):
            actual = session_manager.get_session_state(info.session_id)
            if info.session_id == sid:
                states_seen.append((info.state.value, actual))
            original_emit(info)

        session_manager._emit_state = track

        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("Done")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        # Every emitted state should match get_session_state at that moment
        for emitted, actual in states_seen:
            assert emitted == actual

    def test_has_session_active(self, session_manager, sm_module):
        """has_session returns True for active (non-stopped) sessions."""
        sid = "consist-active"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.WORKING
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        assert session_manager.has_session(sid) is True

    def test_has_session_stopped(self, session_manager, sm_module):
        """has_session returns True even for stopped sessions (still tracked)."""
        sid = "consist-stopped"
        info = sm_module.SessionInfo(
            session_id=sid, state=sm_module.SessionState.STOPPED
        )
        with session_manager._lock:
            session_manager._sessions[sid] = info

        assert session_manager.has_session(sid) is True

    def test_has_session_unknown(self, session_manager):
        """has_session returns False for unknown sessions."""
        assert session_manager.has_session("totally-unknown") is False

    def test_state_transitions_emit_ws_events(
        self, session_manager, sm_module, mock_socketio
    ):
        """Every state transition emits a session_state WebSocket event."""
        sid = "consist-ws"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("Hi")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        state_events = collect_emitted_states(mock_socketio, sid)
        emitted_states = [e["state"] for e in state_events]
        # Must have starting, working, and idle (ResultMessage sets idle, then
        # the message loop exit also checks but finds it already idle)
        assert "starting" in emitted_states
        assert "working" in emitted_states
        assert "idle" in emitted_states

    def test_state_never_stuck_normal_path(self, session_manager, sm_module):
        """Every code path eventually reaches IDLE or STOPPED."""
        sid = "consist-not-stuck"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("Working")]),
            MockAssistantMessage([
                MockToolUseBlock(id="t1", name="Bash", input={"command": "ls"})
            ]),
            MockUserMessage([MockToolResultBlock(tool_use_id="t1", content="file.txt")]),
            MockAssistantMessage([MockTextBlock("Done")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        final = session_manager.get_session_state(sid)
        assert final in ("idle", "stopped")

    def test_state_never_stuck_error_path(self, session_manager, sm_module):
        """Error path always reaches STOPPED."""
        sid = "consist-not-stuck-err"
        mock_client = MockClaudeSDKClient()

        async def fail_connect(prompt=None):
            raise RuntimeError("fail")

        mock_client.connect = fail_connect

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "stopped")

        assert session_manager.get_session_state(sid) == "stopped"

    def test_concurrent_start_and_close_no_deadlock(self, session_manager, sm_module):
        """Concurrent start_session + close_session should not deadlock or crash."""
        errors = []

        def start_and_close(i):
            sid = f"concurrent-sc-{i}"
            try:
                mock_client = MockClaudeSDKClient()
                mock_client._messages = [MockResultMessage(session_id=sid)]

                with patch.object(
                    sm_module, 'ClaudeSDKClient', return_value=mock_client
                ):
                    session_manager.start_session(sid, prompt="x", cwd="/tmp")
                    time.sleep(0.05)
                    session_manager.close_session(sid)
            except Exception as e:
                errors.append((i, str(e)))

        threads = [
            threading.Thread(target=start_and_close, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(errors) == 0, f"Errors: {errors}"

    def test_rapid_state_transitions_no_lost_events(
        self, session_manager, sm_module, mock_socketio
    ):
        """Rapid transitions should not lose WebSocket events."""
        sid = "consist-rapid"
        mock_client = MockClaudeSDKClient()
        # Many messages in rapid succession
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock(f"msg-{i}")]) for i in range(10)
        ] + [MockResultMessage(session_id=sid)]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

        # We should have starting + working + (multiple idle from ResultMessage
        # and loop end) emitted
        state_events = collect_emitted_states(mock_socketio, sid)
        assert len(state_events) >= 3  # at least starting, working, idle

    def test_session_state_dict_includes_permission_in_waiting(
        self, session_manager, sm_module
    ):
        """to_state_dict includes permission details when in WAITING state."""
        sid = "consist-perm-dict"
        info = sm_module.SessionInfo(
            session_id=sid,
            state=sm_module.SessionState.WAITING,
        )
        info.pending_tool_name = "Bash"
        info.pending_tool_input = {"command": "rm -rf /"}
        with session_manager._lock:
            session_manager._sessions[sid] = info

        states = session_manager.get_all_states()
        by_id = {s["session_id"]: s for s in states}
        assert "permission" in by_id[sid]
        assert by_id[sid]["permission"]["tool_name"] == "Bash"
        assert by_id[sid]["permission"]["tool_input"]["command"] == "rm -rf /"

    def test_session_state_dict_no_permission_when_not_waiting(
        self, session_manager, sm_module
    ):
        """to_state_dict does NOT include permission details when not WAITING."""
        sid = "consist-no-perm"
        info = sm_module.SessionInfo(
            session_id=sid,
            state=sm_module.SessionState.IDLE,
        )
        info.pending_tool_name = "Bash"  # stale data
        with session_manager._lock:
            session_manager._sessions[sid] = info

        states = session_manager.get_all_states()
        by_id = {s["session_id"]: s for s in states}
        assert "permission" not in by_id[sid]

    def test_cost_accumulates_across_messages(self, session_manager, sm_module):
        """Cost is updated from the last ResultMessage."""
        sid = "consist-cost"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockResultMessage(session_id=sid, total_cost_usd=0.05),
        ]
        mock_client._response_messages = [
            MockResultMessage(session_id=sid, total_cost_usd=0.12),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="x", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            with session_manager._lock:
                info = session_manager._sessions[sid]
            assert info.cost_usd == pytest.approx(0.05)

            session_manager.send_message(sid, "more")
            wait_for(
                lambda: session_manager._sessions.get(sid)
                and session_manager._sessions[sid].cost_usd > 0.05
            )

        with session_manager._lock:
            info = session_manager._sessions[sid]
        assert info.cost_usd == pytest.approx(0.12)

    def test_entries_accumulate_across_send_messages(self, session_manager, sm_module):
        """Entries from initial session and follow-ups are all in one list."""
        sid = "consist-entries"
        mock_client = MockClaudeSDKClient()
        mock_client._messages = [
            MockAssistantMessage([MockTextBlock("First")]),
            MockResultMessage(session_id=sid),
        ]
        mock_client._response_messages = [
            MockAssistantMessage([MockTextBlock("Second")]),
            MockResultMessage(session_id=sid),
        ]

        with patch.object(sm_module, 'ClaudeSDKClient', return_value=mock_client):
            session_manager.start_session(sid, prompt="init", cwd="/tmp")
            wait_for(lambda: session_manager.get_session_state(sid) == "idle")

            session_manager.send_message(sid, "Follow-up")
            wait_for(lambda: len(session_manager.get_entries(sid)) >= 3)

        entries = session_manager.get_entries(sid)
        texts = [e.get("text", "") for e in entries]
        assert "First" in texts
        assert "Follow-up" in texts
        assert "Second" in texts
