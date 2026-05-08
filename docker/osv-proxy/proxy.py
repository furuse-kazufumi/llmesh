"""OSV API proxy — forwards POST /v1/querybatch to api.osv.dev.

This is the only container with external network egress.
All other paths return 404 so the attack surface is minimal.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

_OSV_TARGET = "https://api.osv.dev/v1/querybatch"
_LISTEN_PORT = 8080
_UPSTREAM_TIMEOUT = 15  # seconds


class _ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/v1/querybatch":
            self._reply(404, b'{"error":"not_found"}')
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._reply(400, b'{"error":"bad_content_length"}')
            return

        body = self.rfile.read(length)

        req = urllib.request.Request(
            _OSV_TARGET,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_UPSTREAM_TIMEOUT) as resp:
                data = resp.read()
            self._reply(200, data)
        except urllib.error.HTTPError as exc:
            err = json.dumps({"error": f"upstream_http:{exc.code}"}).encode()
            self._reply(502, err)
        except urllib.error.URLError as exc:
            err = json.dumps({"error": f"upstream_network:{exc.reason}"}).encode()
            self._reply(502, err)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, b'{"status":"ok"}')
        else:
            self._reply(404, b'{"error":"not_found"}')

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress default access log noise


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", _LISTEN_PORT), _ProxyHandler)
    server.serve_forever()
