#!/usr/bin/env python3
"""Local server for the car tracker.

Serves the site in docs/ and adds /api/refresh, which runs a live sync (see
tracker.py) on every page load. The same site on GitHub Pages reads
docs/data/inventory.json directly, which GitHub Actions updates hourly.

Run:  python3 server.py   then open http://localhost:8777
"""

import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import tracker

PORT = int(os.environ.get("PORT", 8777))
sync_lock = threading.Lock()
last_sync = {"at": None, "summary": None, "error": None}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(tracker.ROOT / "docs"), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    def end_headers(self):
        # Always serve the latest page and data instead of a stale cached copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/refresh":
            # One sync at a time. If a sync is already running (e.g. a quick
            # reload), wait for it and share its result instead of queueing another.
            if not sync_lock.acquire(blocking=False):
                with sync_lock:
                    pass
            else:
                try:
                    started = time.time()
                    summary = tracker.sync()
                    summary["seconds"] = round(time.time() - started, 1)
                    last_sync.update(at=tracker.now(), summary=summary, error=None)
                except Exception as e:
                    last_sync.update(at=tracker.now(), error=str(e))
                finally:
                    sync_lock.release()
            return self.send_json({"sync": last_sync, **tracker.load_data()})
        return super().do_GET()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Car tracker running at http://localhost:{PORT}  (data: {tracker.DATA_PATH})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
