#!/usr/bin/env python3
"""
NLR NSRDB CORS Proxy
--------------------
Forwards requests to the NLR NSRDB API and adds CORS headers
so the solar simulator can call it directly from the browser.

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

# Windows consoles often default to a cp1252 codepage, which can't encode the
# box-drawing / checkmark characters this script prints — force UTF-8 output.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

PORT = 8765
NLR_HOSTS = [
    "developer.nlr.gov",
]

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Clean up console output
        status = args[1] if len(args) > 1 else "?"
        path   = args[0].split(" ")[1] if args else "?"
        color  = "\033[92m" if str(status).startswith("2") else "\033[91m"
        reset  = "\033[0m"
        print(f"  {color}{status}{reset}  {path[:80]}")

    def do_OPTIONS(self):
        """Handle preflight CORS check."""
        self.send_response(200)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        """Forward GET request to NLR, return response with CORS headers."""
        # Expect path like /proxy?url=<encoded-nlr-url>
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/proxy" or "url" not in params:
            self._send_error(400, "Usage: /proxy?url=<encoded-nlr-api-url>")
            return

        target_url = params["url"][0]

        # Safety check — only forward to known NLR hosts
        target_parsed = urllib.parse.urlparse(target_url)
        if target_parsed.hostname not in NLR_HOSTS:
            self._send_error(403, f"Only NLR hosts are allowed. Got: {target_parsed.hostname}")
            return

        print(f"\n→ Proxying to: {target_url[:100]}...")

        last_error = None
        for host in NLR_HOSTS:
            url = target_url.replace(target_parsed.hostname, host, 1)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "NLR-CORS-Proxy/1.0"})
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
                print(f"  ✓ {len(body):,} bytes from {host}")
                return

            except urllib.error.HTTPError as e:
                body = e.read()
                # Pass through HTTP errors (e.g. 403 bad API key) with CORS headers
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                for k, v in CORS_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                print(f"  ✗ HTTP {e.code} from {host}")
                return

            except Exception as e:
                last_error = str(e)
                print(f"  ✗ Failed ({host}): {e}")
                continue

        self._send_error(502, f"All NLR hosts failed: {last_error}")

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
    server = http.server.HTTPServer(("127.0.0.1", PORT), ProxyHandler)
    print(f"""
╔══════════════════════════════════════════════╗
║         NLR NSRDB CORS Proxy                 ║
║         Listening on http://localhost:{PORT}  ║
╚══════════════════════════════════════════════╝

  Requests will be forwarded to developer.nlr.gov

  Press Ctrl+C to stop.
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Proxy stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
