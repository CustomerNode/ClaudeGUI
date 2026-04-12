"""
simulate_sessions.py -- Creates fake .jsonl files and continuously updates them
to simulate Claude working, asking questions, and going idle.

This simulation:
- Creates 6 fake sessions with realistic JSONL content
- Continuously updates them with state transitions
- Cycles through: idle -> working -> question -> working -> idle
- Runs for 90 seconds to test long-running state detection
- Validates state detection at each step using _parse_waiting_state / _parse_session_kind

Usage:
    python tests/simulate_sessions.py [--duration 90] [--sessions-dir /tmp/sim]

If --sessions-dir is not provided, creates a temp directory.
"""

import argparse
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.process_detection import _parse_waiting_state, _parse_session_kind


# ---------------------------------------------------------------------------
# JSONL entry builders
# ---------------------------------------------------------------------------

def assistant_text(text, stop_reason="end_turn"):
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
        }
    }


def assistant_tool_use(name, inp, tool_id="tu_auto"):
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": name, "input": inp, "id": tool_id}],
            "stop_reason": "tool_use",
        }
    }


def user_text(text):
    return {"type": "user", "message": {"content": text}}


def user_tool_result(tool_id="tu_auto", text="ok"):
    return {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]
        }
    }


def progress_entry():
    return {"type": "progress", "data": f"step at {time.time():.2f}"}


# ---------------------------------------------------------------------------
# Session simulator
# ---------------------------------------------------------------------------

class SimulatedSession:
    """Manages a single fake .jsonl session with state transitions."""

    STATES = ["idle", "working", "question_tool", "question_text"]

    def __init__(self, sid: str, path: Path):
        self.sid = sid
        self.path = path
        self.entries = []
        self.state = "idle"
        self.state_start = time.time()
        self.transition_count = 0

    def _write(self, mtime_age=0):
        """Write entries and optionally backdate mtime."""
        lines = [json.dumps(e) for e in self.entries]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if mtime_age > 0:
            t = time.time() - mtime_age
            os.utime(str(self.path), (t, t))

    def _set_state(self, new_state):
        self.state = new_state
        self.state_start = time.time()
        self.transition_count += 1

    def setup_idle(self):
        """Set session to idle state."""
        self.entries = [
            user_text("Help me with something"),
            assistant_text("Done! Everything is complete.", stop_reason="end_turn"),
        ]
        self._write(mtime_age=30)
        self._set_state("idle")

    def setup_working(self):
        """Set session to working state (recent file activity)."""
        self.entries.extend([
            user_text(f"Task {self.transition_count}: Do something new"),
        ])
        self._write(mtime_age=2)  # recent
        self._set_state("working")

    def setup_working_tool_cycle(self):
        """Working via tool_use -> tool_result cycle."""
        tool_names = ["Bash", "Read", "Grep", "Edit", "Write"]
        tool = random.choice(tool_names)
        commands = ["ls -la", "cat file.txt", "grep -r 'TODO'", "npm test", "git status"]
        cmd = random.choice(commands)
        tid = f"tu_{self.transition_count}"
        self.entries.extend([
            assistant_tool_use(tool, {"command": cmd}, tool_id=tid),
            progress_entry(),
            user_tool_result(tid, f"Output of {cmd}"),
        ])
        self._write(mtime_age=3)
        self._set_state("working")

    def setup_question_tool(self):
        """Set session to tool permission question."""
        dangerous_tools = [
            ("Bash", {"command": "rm -rf /tmp/cache"}),
            ("Write", {"path": "src/config.py", "content": "new config"}),
            ("Bash", {"command": "pip install dangerous-pkg"}),
            ("Edit", {"path": "main.py", "old_string": "x=1", "new_string": "x=2"}),
        ]
        tool, inp = random.choice(dangerous_tools)
        self.entries.extend([
            assistant_tool_use(tool, inp, tool_id=f"tu_{self.transition_count}"),
        ])
        self._write(mtime_age=10)  # stale enough to detect
        self._set_state("question_tool")

    def setup_question_text(self):
        """Set session to text question."""
        questions = [
            "Which approach would you prefer? [option A/option B]",
            "Should I continue with the refactoring? [y/n]",
            "Do you want me to:\n1. Create a new module\n2. Modify the existing one\n3. Use a library?",
            "I found 3 potential issues. Should I fix all of them?",
            "The tests are failing. Should I update the snapshots? [y/n/a]",
        ]
        self.entries.extend([
            assistant_text(random.choice(questions)),
        ])
        self._write(mtime_age=10)
        self._set_state("question_text")

    def answer_question(self):
        """Simulate answering a question -> transition to working."""
        tid = f"tu_{self.transition_count}"
        if self.state == "question_tool":
            # Tool was approved, add progress + result
            self.entries.extend([
                progress_entry(),
                user_tool_result(tid, "Tool executed successfully"),
            ])
        else:
            # Text answer
            self.entries.extend([
                user_text("yes"),
            ])
        self._write(mtime_age=3)
        self._set_state("working")

    def verify_state(self):
        """Check that _parse_waiting_state/_parse_session_kind agree with expected state."""
        waiting = _parse_waiting_state(self.path)
        kind = _parse_session_kind(self.path)

        if self.state == "idle":
            expected_waiting = None
            expected_kind = "idle"
        elif self.state == "working":
            expected_waiting = None
            expected_kind = "working"
        elif self.state == "question_tool":
            expected_waiting_kind = "tool"
            expected_kind = None  # doesn't matter, waiting_state takes precedence
        elif self.state == "question_text":
            expected_waiting_kind = "text"
            expected_kind = None

        errors = []

        if self.state in ("idle", "working"):
            if waiting is not None:
                errors.append(f"Expected no waiting state for {self.state}, got: {waiting}")
            if kind != expected_kind:
                errors.append(f"Expected kind={expected_kind}, got kind={kind}")
        elif self.state.startswith("question_"):
            if waiting is None:
                errors.append(f"Expected waiting state for {self.state}, got None")
            elif waiting["kind"] != expected_waiting_kind:
                errors.append(f"Expected waiting kind={expected_waiting_kind}, got {waiting['kind']}")

        return errors


# ---------------------------------------------------------------------------
# Main simulation loop
# ---------------------------------------------------------------------------

def run_simulation(sessions_dir: Path, duration: int = 90):
    """Run the full simulation."""
    print(f"=== Session State Simulation ===")
    print(f"Directory: {sessions_dir}")
    print(f"Duration:  {duration}s")
    print()

    # Create 6 sessions
    session_ids = [
        "sim-0001-alpha-fast",
        "sim-0002-beta-slow",
        "sim-0003-gamma-question",
        "sim-0004-delta-mixed",
        "sim-0005-epsilon-idle",
        "sim-0006-zeta-burst",
    ]

    sessions = []
    for sid in session_ids:
        path = sessions_dir / f"{sid}.jsonl"
        s = SimulatedSession(sid, path)
        s.setup_idle()
        sessions.append(s)

    start = time.time()
    cycle = 0
    total_checks = 0
    total_errors = 0
    error_details = []

    print(f"Created {len(sessions)} sessions, starting simulation...\n")

    # Transition schedule (seconds into simulation -> actions)
    # Each session gets its own schedule for variety
    schedules = {
        0: [  # alpha-fast: rapid cycles
            (2, "working"), (5, "question_tool"), (8, "answer"),
            (12, "working_tools"), (15, "question_text"), (18, "answer"),
            (22, "idle"), (30, "working"), (35, "question_tool"), (38, "answer"),
            (42, "idle"), (50, "working"), (55, "question_tool"), (58, "answer"),
            (62, "idle"), (70, "working"), (75, "idle"),
        ],
        1: [  # beta-slow: long working periods
            (5, "working"), (10, "working_tools"), (20, "working_tools"),
            (30, "question_tool"), (40, "answer"), (50, "working_tools"),
            (60, "idle"), (70, "working"), (80, "idle"),
        ],
        2: [  # gamma-question: lots of questions
            (3, "working"), (6, "question_tool"), (12, "answer"),
            (15, "question_text"), (20, "answer"),
            (25, "question_tool"), (30, "answer"),
            (35, "question_text"), (40, "answer"),
            (45, "question_tool"), (50, "answer"),
            (55, "idle"), (65, "question_text"), (70, "answer"),
            (75, "idle"),
        ],
        3: [  # delta-mixed: varied
            (4, "working"), (8, "question_text"), (14, "answer"),
            (18, "working_tools"), (25, "idle"),
            (30, "working"), (35, "question_tool"), (42, "answer"),
            (48, "idle"), (55, "working"), (65, "idle"),
        ],
        4: [  # epsilon-idle: mostly idle, occasional work
            (10, "working"), (20, "idle"),
            (40, "working"), (45, "question_tool"), (50, "answer"),
            (55, "idle"),
        ],
        5: [  # zeta-burst: burst of activity then long idle
            (2, "working"), (3, "question_tool"), (5, "answer"),
            (6, "question_text"), (8, "answer"),
            (9, "working_tools"), (10, "question_tool"), (12, "answer"),
            (13, "idle"),
        ],
    }

    # Track which schedule items have been executed
    schedule_idx = {i: 0 for i in range(len(sessions))}

    while time.time() - start < duration:
        elapsed = time.time() - start
        cycle += 1

        # Execute scheduled transitions
        for i, session in enumerate(sessions):
            schedule = schedules[i]
            idx = schedule_idx[i]
            while idx < len(schedule) and elapsed >= schedule[idx][0]:
                _, action = schedule[idx]
                if action == "working":
                    session.setup_working()
                elif action == "working_tools":
                    session.setup_working_tool_cycle()
                elif action == "question_tool":
                    session.setup_question_tool()
                elif action == "question_text":
                    session.setup_question_text()
                elif action == "answer":
                    session.answer_question()
                elif action == "idle":
                    session.setup_idle()
                idx += 1
                schedule_idx[i] = idx

        # Verify all sessions
        for session in sessions:
            errors = session.verify_state()
            total_checks += 1
            if errors:
                total_errors += 1
                for err in errors:
                    detail = f"  [{elapsed:.1f}s] {session.sid} (state={session.state}): {err}"
                    error_details.append(detail)
                    print(f"FAIL {detail}")

        # Print periodic status
        if cycle % 10 == 0:
            states = {s.sid.split("-")[2]: s.state for s in sessions}
            states_str = " | ".join(f"{k}={v}" for k, v in states.items())
            print(f"[{elapsed:5.1f}s] cycle={cycle:4d} checks={total_checks} errors={total_errors} | {states_str}")

        time.sleep(1)

    # Final summary
    print(f"\n{'='*60}")
    print(f"SIMULATION COMPLETE")
    print(f"{'='*60}")
    print(f"Duration:     {time.time() - start:.1f}s")
    print(f"Sessions:     {len(sessions)}")
    print(f"Total checks: {total_checks}")
    print(f"Total errors: {total_errors}")
    print(f"Transitions:  {sum(s.transition_count for s in sessions)}")
    if error_details:
        print(f"\nError details:")
        for d in error_details[:20]:
            print(d)
        if len(error_details) > 20:
            print(f"  ... and {len(error_details) - 20} more")
    else:
        print(f"\nAll state checks PASSED")

    print()
    for s in sessions:
        print(f"  {s.sid}: {s.transition_count} transitions, final state={s.state}")

    return total_errors == 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate Claude sessions for testing")
    parser.add_argument("--duration", type=int, default=90, help="Simulation duration in seconds")
    parser.add_argument("--sessions-dir", type=str, help="Directory for session files (default: temp)")
    args = parser.parse_args()

    if args.sessions_dir:
        sdir = Path(args.sessions_dir)
        sdir.mkdir(parents=True, exist_ok=True)
    else:
        sdir = Path(tempfile.mkdtemp(prefix="claude_sim_"))

    success = run_simulation(sdir, args.duration)
    sys.exit(0 if success else 1)
