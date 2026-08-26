"""Own and stop process groups used by conformance internals."""

from __future__ import annotations

import os
import signal
import subprocess
import time


def popen_group_options() -> dict[str, object]:
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}


def _process_group_exists(process: subprocess.Popen) -> bool:
    if os.name != "posix":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_group(process: subprocess.Popen) -> None:
    """Stop the process and inherited group before returning to an audit."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + 0.15
        while _process_group_exists(process) and time.monotonic() < deadline:
            time.sleep(0.01)
        if _process_group_exists(process):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    elif process.poll() is None:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except (AttributeError, OSError):
            process.terminate()
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
