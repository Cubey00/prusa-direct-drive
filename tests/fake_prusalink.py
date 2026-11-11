"""A small stand-in for the printer's PrusaLink API (v1), for tests. Checks the X-Api-Key header."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = "testkey"


class FakePrinter:
    def __init__(self):
        self.state = "IDLE"
        self.job = None              # dict with id, progress, ... while a job exists
        self.files = {}              # remote path -> bytes
        self.uploads = []            # (path, headers dict)
        self.calls = []              # (method, path)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def host(self):
        return f"127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                if self.headers.get("X-Api-Key") != API_KEY:
                    self._send(401)
                    return False
                fake.calls.append((self.command, self.path))
                return True

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/api/v1/status":
                    body = {"printer": {"state": fake.state, "temp_nozzle": 215.0, "temp_bed": 60.0}}
                    if fake.job:
                        body["job"] = {k: fake.job[k] for k in ("id", "progress", "time_remaining",
                                                                "time_printing")}
                    self._send(200, body)
                elif self.path == "/api/v1/job":
                    if not fake.job:
                        self._send(204)
                    else:
                        self._send(200, {**fake.job, "state": fake.state,
                                         "file": {"name": fake.job["name"], "display_name": fake.job["name"]}})
                elif self.path == "/api/v1/storage":
                    self._send(200, {"storage_list": [
                        {"type": "LOCAL", "path": "/local", "available": False, "read_only": False},
                        {"type": "USB", "path": "/usb", "name": "usb", "available": True, "read_only": False}]})
                else:
                    self._send(404)

            def do_PUT(self):
                if not self._auth():
                    return
                length = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(length)
                if self.path.startswith("/api/v1/files/"):
                    remote = self.path[len("/api/v1/files"):]
                    if fake.state not in ("IDLE", "READY", "FINISHED", "STOPPED"):
                        self._send(409)
                        return
                    fake.files[remote] = data
                    fake.uploads.append((remote, dict(self.headers)))
                    if self.headers.get("Print-After-Upload") == "?1":
                        fake.state = "PRINTING"
                        fake.job = {"id": 7, "progress": 0.0, "time_remaining": 600, "time_printing": 0,
                                    "name": remote.rsplit("/", 1)[-1]}
                    self._send(201)
                elif self.path.endswith("/pause") and fake.job:
                    fake.state = "PAUSED"
                    self._send(204)
                elif self.path.endswith("/resume") and fake.job:
                    fake.state = "PRINTING"
                    self._send(204)
                else:
                    self._send(404)

            def do_DELETE(self):
                if not self._auth():
                    return
                if fake.job and self.path == f"/api/v1/job/{fake.job['id']}":
                    fake.state, fake.job = "STOPPED", None
                    self._send(204)
                else:
                    self._send(404)

        return Handler
