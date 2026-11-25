"""Cooperative device ownership across this application's processes and threads.

OS locks are released on process exit. The file must not be unlinked while in use.
Locks cover application clients; external software still needs operator exclusion.
"""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time


class DeviceBusy(RuntimeError):
    pass


_guard = threading.Lock()
_held = set()


def _owner_path(resource):
    return Path(tempfile.gettempdir()) / f"prusa-direct-drive-{resource}.owner"


def _describe_owner(resource):
    """Who holds the lease, from the note the owner left (PID, program, since when), or ''."""
    try:
        pid, since, prog = _owner_path(resource).read_text(encoding="utf-8").split("\n", 2)
    except (OSError, ValueError):
        return ""
    what = "the hidden calibrator of a path programmer" if "--link" in prog else prog.strip()
    return f" (PID {pid}, {what}, since {since}; close it, or end that PID in Task Manager)"


class DeviceLease:
    def __init__(self, resource="printer"):
        if resource not in {"printer", "camera"}:
            raise ValueError("Unknown device resource")
        self.resource = resource
        self.file = None

    def acquire(self):
        with _guard:
            if self.file is not None or self.resource in _held:
                raise DeviceBusy(f"{self.resource} is already owned by another operation")
            path = Path(tempfile.gettempdir()) / f"prusa-direct-drive-{self.resource}.lock"
            handle = path.open("a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    if not handle.read(1):
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise DeviceBusy(f"{self.resource} is owned by another application process"
                                 + _describe_owner(self.resource)) from None
            self.file = handle
            try:                       # a note for whoever is refused next: who holds it
                _owner_path(self.resource).write_text(
                    f"{os.getpid()}\n{time.strftime('%H:%M:%S')}\n{' '.join(sys.argv)}", encoding="utf-8")
            except OSError:
                pass
            _held.add(self.resource)
        return self

    def release(self):
        with _guard:
            if self.file is None:
                return
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None
            _held.discard(self.resource)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()
