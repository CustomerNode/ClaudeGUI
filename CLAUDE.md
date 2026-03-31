# ClaudeGUI Project Rules

## Server restarts
To restart the server after Python changes, call `POST /api/restart` via curl:
```bash
curl -s -X POST http://localhost:5050/api/restart
```
This triggers a clean restart through the app's built-in restart endpoint. Wait a few seconds for it to come back up.

Do NOT use subprocess, os.system, taskkill, or any other method to start, stop, or manage server processes directly. No terminal window spawning.

## Slash commands are intercepted client-side
Claude CLI slash commands (e.g. `/compact`, `/rewind`, `/clear`) are NOT sent to the SDK. They get silently eaten with no response, leaving the session stuck idle. Instead, `_interceptSlashCommand()` in `live-panel.js` catches them at every submit path and either triggers the GUI equivalent (e.g. `/rewind` clicks the Rewind toolbar button, `/compact` fires `liveCompact()`) or shows a toast explaining the command isn't supported in the GUI. The command map lives in `_slashCommandMap`. Messages with `/` that aren't bare commands (e.g. "fix /etc/config") pass through normally.
