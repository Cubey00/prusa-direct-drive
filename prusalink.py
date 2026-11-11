"""
prusalink.py
Network link to the printer's PrusaLink web API (v1): upload a sliced plate, start it, follow the
print and stop/pause/resume it. Separate from gantry_controls (USB serial): printing runs from the
printer's own storage, so the laptop and the USB cable are free while it prints.

Auth is HTTP digest with the user and password shown on the printer (Settings > Network >
PrusaLink, user "maker" by default). An API key (X-Api-Key) is accepted as well, for firmware
that offers one. Nothing here touches the network until a method is called.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import requests
from requests.auth import HTTPDigestAuth

DEFAULT_USER = "maker"
TIMEOUT = 5.0               # s for status calls
UPLOAD_TIMEOUT = 300.0      # s for an upload
IDLE_STATES = {"IDLE", "READY", "FINISHED", "STOPPED"}       # a new print may start
ACTIVE_STATES = {"PRINTING", "PAUSED", "BUSY", "ATTENTION"}  # a job is running or needs the user


class PrusaLinkError(Exception):
    pass


@dataclass
class PrintStatus:
    state: str                      # printer state: IDLE, PRINTING, PAUSED, FINISHED, ...
    job_id: int = None
    progress: float = None          # %
    time_remaining: int = None      # s
    time_printing: int = None       # s
    file: str = None                # display name of the printed file
    temp_nozzle: float = None
    temp_bed: float = None

    @property
    def active(self):
        return self.state in ACTIVE_STATES

    @property
    def can_start(self):
        return self.state in IDLE_STATES


def safe_name(name):
    """File name the printer accepts: letters, digits, '_', '-', '.'; nothing else."""
    stem, dot, ext = Path(name).name.rpartition(".")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem if dot else ext).strip("_") or "plate"
    return f"{stem}.{ext}" if dot else stem


def fmt_duration(seconds):
    if seconds is None:
        return "--"
    seconds = int(seconds)
    h, m = divmod(seconds // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{seconds % 60:02d}s"


class PrusaLink:
    def __init__(self, host, password=None, user=DEFAULT_USER, api_key=None, session=None):
        host = host.strip().rstrip("/")
        if not host:
            raise PrusaLinkError("No printer address set.")
        self.base = host if host.startswith(("http://", "https://")) else f"http://{host}"
        self.session = session or requests.Session()
        if api_key:
            self.session.headers["X-Api-Key"] = api_key
        elif password:
            self.session.auth = HTTPDigestAuth(user or DEFAULT_USER, password)

    # ---------------- HTTP ----------------
    def _request(self, method, path, timeout=TIMEOUT, ok=(200, 201, 204), **kw):
        url = self.base + path
        try:
            r = self.session.request(method, url, timeout=timeout, **kw)
        except requests.ConnectionError:
            raise PrusaLinkError(f"Cannot reach the printer at {self.base}. Check the address and that "
                                 "PrusaLink is enabled (Settings > Network on the printer).") from None
        except requests.Timeout:
            raise PrusaLinkError(f"The printer at {self.base} did not answer in time.") from None
        if r.status_code == 401:
            raise PrusaLinkError("PrusaLink refused the login: check the user and password "
                                 "(Settings > Network > PrusaLink on the printer).")
        if r.status_code not in ok:
            detail = r.text.strip()[:200]
            raise PrusaLinkError(f"{method} {path} -> HTTP {r.status_code}" + (f": {detail}" if detail else ""))
        return r

    # ---------------- reading ----------------
    def status(self):
        d = self._request("GET", "/api/v1/status").json()
        p, j = d.get("printer", {}), d.get("job") or {}
        st = PrintStatus(state=p.get("state", "UNKNOWN"), job_id=j.get("id"), progress=j.get("progress"),
                         time_remaining=j.get("time_remaining"), time_printing=j.get("time_printing"),
                         temp_nozzle=p.get("temp_nozzle"), temp_bed=p.get("temp_bed"))
        if st.job_id is not None:
            st.file = self.job_file()
        return st

    def job_file(self):
        r = self._request("GET", "/api/v1/job")
        if r.status_code == 204 or not r.content:
            return None
        f = r.json().get("file") or {}
        return f.get("display_name") or f.get("name")

    def storage_path(self):
        """Path of the first available, writable storage, e.g. '/usb'."""
        for s in self._request("GET", "/api/v1/storage").json().get("storage_list", []):
            if s.get("available") and not s.get("read_only"):
                return "/" + s["path"].strip("/")
        raise PrusaLinkError("The printer has no writable storage available (USB stick inserted?).")

    # ---------------- printing ----------------
    def upload(self, gcode, name=None, print_after=True, overwrite=True):
        """Upload a (b)gcode file; with print_after the printer starts it right away.
        Refuses when the printer is busy. Returns the remote path."""
        gcode = Path(gcode)
        if not gcode.exists():
            raise PrusaLinkError(f"{gcode} does not exist.")
        if print_after:
            st = self.status()
            if not st.can_start:
                raise PrusaLinkError(f"The printer is {st.state}; finish or stop that first.")
        remote = f"{self.storage_path()}/{safe_name(name or gcode.name)}"
        data = gcode.read_bytes()
        headers = {"Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
                   "Print-After-Upload": "?1" if print_after else "?0",
                   "Overwrite": "?1" if overwrite else "?0"}
        r = self._request("PUT", "/api/v1/files" + remote, timeout=UPLOAD_TIMEOUT, data=data,
                          headers=headers, ok=(200, 201, 204, 409))
        if r.status_code == 409:
            raise PrusaLinkError("The printer refused the upload (HTTP 409): it is busy, or a file "
                                 f"named {remote} is being printed.")
        return remote

    def stop(self, job_id):
        self._request("DELETE", f"/api/v1/job/{int(job_id)}")

    def pause(self, job_id):
        self._request("PUT", f"/api/v1/job/{int(job_id)}/pause")

    def resume(self, job_id):
        self._request("PUT", f"/api/v1/job/{int(job_id)}/resume")
