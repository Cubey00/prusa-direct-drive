"""Background TIFF writer for run images (same idea as imaging.py's saver thread). No Qt."""
import queue
import threading
from pathlib import Path

import tifffile


class ImageSaver:
    def __init__(self):
        self._q = queue.Queue()
        self.errors = []
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    def put(self, array, path):
        self._q.put((array, Path(path)))

    def _work(self):
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                return
            arr, path = item
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tifffile.imwrite(path, arr)
            except Exception as e:
                self.errors.append(f"{path.name}: {e}")
            self._q.task_done()

    def close(self):
        """Waits until everything queued is on disk."""
        self._q.put(None)
        self._thread.join()
