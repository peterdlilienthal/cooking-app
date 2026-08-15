/**
 * NLR NSRDB CORS Proxy — Cloudflare Worker
 * -----------------------------------------
 * Forwards requests to the NLR NSRDB API and adds CORS headers so the solar
 * simulator can call it directly from the browser. JS port of nlr_proxy.py,
 * for deployment on Cloudflare Workers instead of running locally.
 *
 * Deploy:
 *   npx wrangler deploy
 *
 * Then point solar_sim_nrel.html's proxy URL at the deployed
 * https://<worker>.<subdomain>.workers.dev instead of localhost:8765.
 */

const NLR_HOST = "developer.nlr.gov";

// NLR enforces a short burst rate limit on top of its hourly quota, so two
// calls fired back-to-back (dataset discovery, then the CSV download) can
// occasionally trip it even when nowhere near the hourly cap. Retry those
// transient failures here instead of surfacing them to the user.
const MAX_ATTEMPTS = 3;
const RETRYABLE_STATUS_CODES = new Set([429, 500, 502, 503, 504]);
const RETRY_BACKOFF_SECONDS = [2, 4]; // wait before attempt 2, then before attempt 3

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

async function handleProxy(request) {
  const url = new URL(request.url);

  // Expect path like /proxy?url=<encoded-nlr-url>
  if (url.pathname !== "/proxy" || !url.searchParams.has("url")) {
    return jsonError(400, "Usage: /proxy?url=<encoded-nlr-api-url>");
  }

  const targetUrl = url.searchParams.get("url");

  let targetParsed;
  try {
    targetParsed = new URL(targetUrl);
  } catch {
    return jsonError(400, "Invalid url parameter");
  }

  // Safety check — only forward to the known NLR host
  if (targetParsed.hostname !== NLR_HOST) {
    return jsonError(403, `Only ${NLR_HOST} is allowed. Got: ${targetParsed.hostname}`);
  }

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const upstream = await fetch(targetUrl, {
        headers: { "User-Agent": "NLR-CORS-Proxy/1.0" },
      });

      if (!upstream.ok) {
        if (RETRYABLE_STATUS_CODES.has(upstream.status) && attempt < MAX_ATTEMPTS) {
          const retryAfter = upstream.headers.get("Retry-After");
          const wait = retryAfter ? parseFloat(retryAfter) : RETRY_BACKOFF_SECONDS[attempt - 1];
          await sleep(wait * 1000);
          continue;
        }

        // Pass through HTTP errors (e.g. 403 bad API key) with CORS headers
        const body = await upstream.arrayBuffer();
        return new Response(body, {
          status: upstream.status,
          headers: { "Content-Type": "application/json", ...CORS_HEADERS },
        });
      }

      const body = await upstream.arrayBuffer();
      const contentType = upstream.headers.get("Content-Type") || "text/plain";
      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": contentType,
          "Content-Length": String(body.byteLength),
          ...CORS_HEADERS,
        },
      });
    } catch (e) {
      return jsonError(502, `Request to ${NLR_HOST} failed: ${e.message || e}`);
    }
  }
}

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 200, headers: CORS_HEADERS });
    }
    if (request.method !== "GET") {
      return jsonError(405, "Only GET and OPTIONS are supported");
    }
    return handleProxy(request);
  },
};
