#strain_gauge
import re, socket, threading, json, time
from collections import deque
from pathlib import Path
import numpy as np

# grams = SCALE * tared_reading, edit calibration.json to change it (fit found with the GUI)
CALIB_FILE = Path(__file__).resolve().parent / "calibration.json"
try:
    SCALE = json.loads(CALIB_FILE.read_text())["scale"]
except (OSError, KeyError, ValueError) as e:
    SCALE = 1.0
    print(f"No valid {CALIB_FILE.name} ({e}), load cell values are raw, not grams")

_RE = re.compile(r'\bloadcell r=(-?\d+)i,o=(-?\d+)i,s=(-?\d+(?:\.\d+)?(?:e-?\d+)?)')
_POS_RE = re.compile(r'\bpos_([xyz]) v=(-?\d+(?:\.\d+)?)')


#ADJUST HISTORY TO CHANGE HOW LONG STRAIN GAUGE DATA WILL BE DISPLAYED
class StrainGauge:
    TARE_SAMPLES = 300

    def __init__(self, port=8514, history=900, debug=False):
        self.pos = {"x": None, "y": None, "z": None}
        self.port, self.debug = port, debug
        self.samples = deque(maxlen=history)   # absolute values, appended by the UDP thread
        self.offset = 0.0                      # tare
        self._count = 0                        # samples received in total
        self._seen = 0                         # _count at the last drain()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def can_tare(self):
        return len(self.samples) >= self.TARE_SAMPLES

    def tare(self):
        """Returns True if the tare was applied, False if not enough samples yet."""
        if not self.can_tare():
            return False
        recent = self.recent(self.TARE_SAMPLES)
        self.offset = sum(recent) / len(recent)
        return True

    def tare_fresh(self, n=None, timeout=5.0):
        """Tare on n samples that all arrive after this call (e.g. once a move has finished),
        so nothing from before is averaged in. Returns False if they don't arrive in time."""
        n = n or self.TARE_SAMPLES
        if not self.wait_samples(n, timeout):
            return False
        recent = self.recent(n)
        self.offset = sum(recent) / len(recent)
        return True

    def wait_samples(self, n, timeout=5.0):
        """Block until n samples have arrived after this call. False on timeout."""
        start = self._count
        deadline = time.monotonic() + timeout
        while self._count - start < n:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.002)
        return True

    def tare_to(self, value):
        self.offset = value

    def recent(self, n):
        """Thread-safe copy of the last n absolute samples (the UDP thread appends to the deque)."""
        for _ in range(5):
            try:
                return list(self.samples)[-n:]
            except RuntimeError:        # deque mutated during iteration
                continue
        return []

    def drain(self):
        """True if new samples arrived since the last call."""
        new = self._count != self._seen
        self._seen = self._count
        return new

    @staticmethod
    def grams(value):
        """Convert a tared reading (or list of them) to grams."""
        if isinstance(value, (list, tuple)):
            return [SCALE * v for v in value]
        return SCALE * value

    def force_g(self, n=300):
        """Tared force in grams, averaged over the last n samples. None if no data yet."""
        s = self.recent(n)
        if not s:
            return None
        return self.grams(sum(s) / len(s) - self.offset)

    def view_smoothed(self, window=32):
        """Tared samples, smoothed with a trailing moving average."""
        vals = np.array(self.recent(len(self.samples)))
        if not len(vals):
            return []
        window = max(1, int(window))
        sums = np.cumsum(vals)
        sums[window:] -= sums[:-window].copy()
        counts = np.minimum(np.arange(1, len(vals) + 1), window)
        return (sums / counts - self.offset).tolist()

    def _worker(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                text = data.decode(errors="replace")
                if self.debug:
                    print("RAW:", text)
                for line in text.splitlines():
                    self._parse(line)

    def _parse(self, line):
        if m := _RE.search(line):
            # raw reading only: the firmware's own offset (o=) is re-zeroed whenever the printer
            # homes or calibrates with the load cell, which would shift our tare by that jump and
            # trip the force guard. Our tare removes the constant part anyway, so the
            # calibration.json scale (fitted on tared readings) still holds.
            raw, scale = int(m.group(1)), float(m.group(3))
            self.samples.append(raw * scale)
            self._count += 1
        elif pm := _POS_RE.search(line):
            self.pos[pm.group(1)] = float(pm.group(2))
