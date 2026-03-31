"""
Main route -- serves the single-page application.
"""

import os
import subprocess
import sys

from flask import Blueprint, jsonify, render_template

bp = Blueprint('main', __name__)


@bp.route("/")
def index():
    return render_template('index.html')


@bp.route("/api/restart", methods=["POST"])
def restart_server():
    """Kill ports 5050/5051 and relaunch run.py in a detached process.

    The current process will be killed by the port cleanup, so the
    response may not arrive — the frontend should just wait and reload.
    """
    try:
        run_py = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "run.py")
        run_py = os.path.abspath(run_py)
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable

        project_dir = os.path.dirname(run_py)

        # PowerShell restart script that:
        # 1. Loops to kill ALL processes on ports 5050/5051 until ports are clear
        # 2. Purges all __pycache__ so fresh code is loaded
        # 3. Launches run.py
        restart_cmd = (
            "powershell -NoProfile -Command \""
            "$maxTries = 10; "
            "for ($i = 0; $i -lt $maxTries; $i++) { "
            "  $pids = @(Get-NetTCPConnection -LocalPort 5050,5051 -ErrorAction SilentlyContinue | "
            "    Select-Object -ExpandProperty OwningProcess -Unique); "
            "  if ($pids.Count -eq 0) { break }; "
            "  $pids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; "
            "  Start-Sleep -Milliseconds 500 "
            "}; "
            f"Get-ChildItem -Path '{project_dir}' -Recurse -Directory -Filter '__pycache__' | "
            "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; "
            "Start-Sleep -Seconds 1; "
            f"Start-Process -FilePath '{pythonw}' -ArgumentList '\"{run_py}\"' "
            f"-WorkingDirectory '{project_dir}'"
            "\""
        )

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )

        subprocess.Popen(
            restart_cmd,
            shell=True,
            creationflags=creation_flags,
        )

        return jsonify({"ok": True, "message": "Restarting..."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
