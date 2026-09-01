#!/usr/bin/env python3
"""
Solar data CORS Proxy
---------------------
Forwards requests to the solar-data APIs the simulator uses (NLR NSRDB and
PVGIS) and adds CORS headers so the page can call them directly from the
browser. Neither upstream sends CORS headers, so a static-hosted page can't
reach them without a proxy like this.

Usage:
    python nlr_proxy.py

Then open the solar simulator — it will automatically use
http://localhost:8765 as the proxy.

Requirements: Python 3.6+ (no extra packages needed)
"""

import http.server
import urllib.request
import urllib.parse
import urllib.error
import json
import sys
import time

# Windows consoles often default to a cp1252 codepage, which can't encode the
# box-drawing / checkmark characters this script prints — force UTF-8 output.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

PORT = 8765

# Hosts this proxy will forward to. NLR is the legacy NSRDB path; PVGIS
# (re.jrc.ec.europa.eu) is the tilted-array replacement being built on the
# `pvgis` branch. Anything else is rejected with a 403.
ALLOWED_HOSTS = {"developer.nlr.gov", "re.jrc.ec.europa.eu"}

# NLR enforces a short burst rate limit on top of its hourly quota, so two
# calls fired back-to-back (dataset discovery, then the CSV download) can
# occasionally trip it even when nowhere near the hourly cap. Retry those
# transient failures here instead of surfacing them to the user.
MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_BACKOFF_SECONDS = [2, 4]  # wait before attempt 2, then before attempt 3

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler routes two shapes through here: log_request()
        # passes ('"GET /x HTTP/1.1"', code, size) — a splittable request line —
        # while send_error()/log_error() pass a printf format with HTTPStatus/str
        # args and no request line. Only format the first shape specially.
        first = args[0] if args else ""
        if isinstance(first, str) and " " in first:
            status = args[1] if len(args) > 1 else "?"
            path   = first.split(" ")[1]
            color  = "\033[92m" if str(status).startswith("2") else "\033[91m"
            print(f"  {color}{status}\033[0m  {path[:80]}")
        else:
            try:
                print(f"  \033[91m{fmt % args}\033[0m")
            except Exception:
                print(f"  {fmt} {args}")

    def do_OPTIONS(self):
        """Handle preflight CORS check."""
        self.send_response(200)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        """Forward GET request to an allowed upstream, return it with CORS headers."""
        # Expect path like /proxy?url=<encoded-upstream-url>
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/proxy" or "url" not in params:
            self._send_error(400, "Usage: /proxy?url=<encoded-upstream-api-url>")
            return

        target_url = params["url"][0]

        # Safety check — only forward to a known upstream host
        target_parsed = urllib.parse.urlparse(target_url)
        target_host = target_parsed.hostname
        if target_host not in ALLOWED_HOSTS:
            self._send_error(403, f"Host not allowed: {target_host}. Allowed: {', '.join(sorted(ALLOWED_HOSTS))}")
            return

        print(f"\n→ Proxying to: {target_url[:100]}...")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                req = urllib.request.Request(target_url, headers={"User-Agent": "NLR-CORS-Proxy/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    content_type = resp.headers.get("Content-Type", "text/plain")
                    body = resp.read()

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for k, v in CORS_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                print(f"  ✓ {len(body):,} bytes from {target_host}")
                return

            except urllib.error.HTTPError as e:
                if e.code in RETRYABLE_STATUS_CODES and attempt < MAX_ATTEMPTS:
                    wait = float(e.headers.get("Retry-After", RETRY_BACKOFF_SECONDS[attempt - 1]))
                    print(f"  ⏳ HTTP {e.code} from {target_host} — retrying in {wait:.0f}s (attempt {attempt}/{MAX_ATTEMPTS})")
                    time.sleep(wait)
                    continue

                body = e.read()
                # Pass through HTTP errors (e.g. 403 bad API key) with CORS headers
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                for k, v in CORS_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                print(f"  ✗ HTTP {e.code} from {target_host}")
                return

            except Exception as e:
                self._send_error(502, f"Request to {target_host} failed: {e}")
                return

    def _send_error(self, code, message):
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def main():
    # Threaded: the page fires the year probe and the year download back to back,
    # and a slow upstream response shouldn't wedge every later request behind it.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    print(f"""
╔══════════════════════════════════════════════╗
║         Solar data CORS Proxy               ║
║         Listening on http://localhost:{PORT}  ║
╚══════════════════════════════════════════════╝

  Requests will be forwarded to: {', '.join(sorted(ALLOWED_HOSTS))}

  Press Ctrl+C to stop.
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Proxy stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
