# VibeNode

A local web interface for managing Claude Code sessions — built by [CustomerNode](https://customernode.com) and [Claude Code](https://claude.ai/download).

## What it does

- Lists all your Claude Code sessions with live state (Working / Idle / Question / Sleeping)
- Live terminal panel — watch Claude work in real time
- Answer Claude's questions directly from the browser (with clickable option buttons)
- Send commands to running sessions
- Session tools: auto-name, duplicate, fork, rewind, delete, summarize, extract code, compare sessions

## Requirements

- Python 3.10+
- Claude Code installed and at least one session created
- Windows (uses PowerShell for process detection and input)

## Setup (AI-assisted — recommended)

If you have [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed, open your terminal and tell Claude:

> Get me set up with https://github.com/CustomerNode/VibeNode

Claude handles the rest — cloning the repo, installing Python and Flask if needed, creating a desktop shortcut, and launching VibeNode for you.

See [FileTaskNode](https://github.com/CustomerNode/FileTaskNode) for an example of a Claude Code workspace built around this kind of AI-assisted setup.

## Setup (manual)

### 1. Clone and install

```bash
git clone https://github.com/CustomerNode/VibeNode.git
cd VibeNode
pip install flask
```

### 2. Run

```bash
python session_manager.py
```

The browser opens automatically to http://localhost:5050.

### 3. Desktop shortcut (Windows)

Run this once in PowerShell to create a desktop shortcut that launches VibeNode with one click:

```powershell
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\VibeNode.lnk")
$Shortcut.TargetPath = (Get-Command pythonw).Source
$Shortcut.Arguments = "`"$env:USERPROFILE\Documents\VibeNode\session_manager.py`""
$Shortcut.WorkingDirectory = "$env:USERPROFILE\Documents\VibeNode"
$Shortcut.IconLocation = "$env:USERPROFILE\Documents\VibeNode\vibenode.ico,0"
$Shortcut.WindowStyle = 7
$Shortcut.Save()
```

Uses `pythonw.exe` (windowless) so no console flashes on launch. The app self-heals this shortcut on every startup — if you created it with `python` instead, it will be silently upgraded to `pythonw` next time you run VibeNode.

## Platform support

**Currently Windows only.** VibeNode relies on Windows-specific features:

- **PowerShell SendKeys** for sending input to Claude terminal sessions
- **`pythonw.exe`** for windowless background processes
- **`netstat` / `taskkill`** for process management and port cleanup
- **PowerShell COM objects** for desktop shortcut creation

### Running on macOS or Linux (not yet supported)

The core web UI (Flask + SocketIO) is cross-platform. The platform-specific parts that would need replacement:

| Feature | Windows (current) | macOS / Linux (needed) |
|---|---|---|
| Send input to sessions | PowerShell SendKeys | `tmux send-keys`, `osascript`, or PTY pipes |
| Background launch | `pythonw.exe` + `CREATE_NO_WINDOW` | `nohup` / `launchd` / `systemd` |
| Process management | `netstat -ano` + `taskkill` | `lsof -i` + `kill` |
| Desktop shortcut | PowerShell COM `.lnk` | `.desktop` file (Linux) / Automator (macOS) |
| Port kill on restart | `Get-NetTCPConnection` | `lsof -ti :PORT \| xargs kill` |

A macOS or Linux port would primarily need a platform adapter for `run.py`, `session_manager.py`, and the daemon's process detection. The web UI, kanban board, Supabase integration, and all frontend code work as-is.

**Contributions welcome** — if you're interested in adding macOS or Linux support, see [CONTRIBUTING.md](CONTRIBUTING.md) or open an issue.

## Notes

- Sessions are read from `~/.claude/projects/`
- Input is sent to Claude terminals via PowerShell SendKeys
- No data leaves your machine (unless you enable Supabase cloud storage for tasks)
