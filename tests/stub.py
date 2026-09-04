"""A stand-in for Jira or GitHub.

Canned answers by method and path, and a record of every request the
agent sent — so a test asserts on the wire (the auth header, the JSON
body, the query) exactly as the real service would see it. The agent
runs in its own worker process, so this is a real HTTP server on
loopback rather than a patched client.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class StubServer:

    def __init__(self):
        self.routes = {}
        self.requests = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def on(self, method, path, body, status=200,
           content_type="application/json"):
        """Answer ``method path`` with ``body`` — a JSON value, or a
        string sent as-is under ``content_type``."""
        self.routes[(method.upper(), path)] = (status, body, content_type)

    def sent(self, method, path):
        """Every request the agent made to ``method path``, in order."""
        return [r for r in self.requests
                if r["method"] == method.upper() and r["path"] == path]

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw) if raw else None
                except ValueError:
                    payload = raw.decode("utf-8", "replace")
                stub.requests.append({
                    "method": self.command,
                    "path": parts.path,
                    "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "json": payload,
                })
                route = stub.routes.get((self.command, parts.path))
                if route is None:
                    status, body, content_type = 404, {
                        "errorMessages": [f"No stub for {self.command} {parts.path}"],
                        "message": "Not Found",
                    }, "application/json"
                else:
                    status, body, content_type = route
                data = (body.encode("utf-8") if isinstance(body, str)
                        else json.dumps(body).encode("utf-8"))
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

        return Handler
