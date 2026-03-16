# ClaudeGUI

A GUI for Claude Code, developed by CustomerNode LLC and independent contributors.

A local web interface for managing Claude Code sessions.

## What it does

- Lists all your Claude Code sessions with live state (Working ⛏️ / Idle 💻 / Question 🙋 / Sleeping 😴)
- Live terminal panel — watch Claude work in real time
- Answer Claude's questions directly from the browser (with clickable option buttons)
- Send commands to running sessions
- Session tools: auto-name, duplicate, delete, summarize, extract code, compare sessions

## Requirements

- Python 3.10+
- Claude Code installed and at least one session created
- Windows (uses PowerShell for process detection and input)

## Setup

```bash
pip install flask
python session_manager.py
```

Then open http://localhost:5050 in your browser.

## Notes

- Sessions are read from `~/.claude/projects/`
- Input is sent to Claude terminals via PowerShell SendKeys
- No data leaves your machine
