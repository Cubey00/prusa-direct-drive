"""Cooperative device ownership across this application's processes and threads.

OS locks are released on process exit. The file must not be unlinked while in use.
Locks cover application clients; external software still needs operator exclusion.
"""
import os
from pathlib import Path
import tempfile
import threading


class DeviceBusy(RuntimeError):
    pass


_guard = threading.Lock()
_held = set()


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
                raise DeviceBusy(f"{self.resource} is owned by another application process") from None
            self.file = handle
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
