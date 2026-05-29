"""
OS command execution tool.

Maintains a persistent working directory (_cwd) across calls within the same
task so the LLM can cd into directories and run subsequent commands there.
"""

import subprocess
import threading
from pathlib import Path

_cwd: str = str(Path(__file__).resolve().parent.parent)
_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()


def run(
    command: str,
    timeout: float = 60,
    kill_event: threading.Event | None = None,
) -> str:
    global _cwd, _proc

    cmd = command.strip()

    # Handle cd separately — subprocess can't persist directory changes
    if cmd.lower() == "cd" or cmd.lower().startswith("cd "):
        target = cmd[2:].strip().strip('"').strip("'")
        if not target or target == "~":
            new_path = Path.home()
        elif target == "..":
            new_path = Path(_cwd).parent
        else:
            new_path = (Path(_cwd) / target).resolve()

        if new_path.is_dir():
            _cwd = str(new_path)
            return f"[cwd] {_cwd}"
        return f"[error] Directory not found: {target}"

    exit_status = "ok"
    try:
        with _proc_lock:
            _proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        lines: list[str] = []
        for line in _proc.stdout:
            if kill_event and kill_event.is_set():
                break
            lines.append(line.rstrip())

        if kill_event and kill_event.is_set():
            _proc.kill()
            return "[cancelled]"

        try:
            _proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _proc.kill()
            lines.append(f"[timeout after {timeout:.0f}s]")
            exit_status = "timeout"

        if _proc.returncode and exit_status == "ok":
            exit_status = "error"

        result = "\n".join(lines) or "(no output)"
        return result

    except Exception as e:
        return f"[error] {e}"

    finally:
        with _proc_lock:
            _proc = None


def cancel() -> None:
    with _proc_lock:
        p = _proc
    if p:
        p.kill()


def reset_cwd() -> None:
    global _cwd
    _cwd = str(Path(__file__).resolve().parent.parent)


