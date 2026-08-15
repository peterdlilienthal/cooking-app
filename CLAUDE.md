# Off-Grid Solar Simulator

A browser-based tool for sizing an off-grid solar + battery system. It pulls a
real year of hourly solar irradiance from NREL's NSRDB, simulates battery
state hour-by-hour against a user-defined load, and compares many PV/battery
size combinations side by side (reliability and cost).

## Files

- **`index.html`** — the entire app: UI, NSRDB fetch logic, the hour-by-hour
  simulation, and all rendering. Single file, no build step, no external JS
  dependencies (only a Google Fonts CSS import). Named `index.html` (not
  `solar_sim_nrel.html`) so GitHub Pages serves it at the site root.
- **`nlr_proxy.py`** — a stdlib-only local CORS proxy (`http://localhost:8765`)
  that forwards browser requests to NREL's API. Required because NREL doesn't
  send CORS headers, so the page can't call it directly. Used for local dev
  (`file://`); the hosted GitHub Pages copy needs a public proxy instead — see
  Hosting below.

## Running it locally

```bash
python nlr_proxy.py          # start the proxy (leave running)
```
Then open `index.html` directly in a browser (`file://` works fine — the
proxy is what needs a real HTTP server, not the page itself).

## Hosting (GitHub Pages)

The repo is served via GitHub Pages (Settings → Pages → Deploy from branch
`master` / root) — every push to `master` redeploys automatically, no build
step or Actions workflow involved.

GitHub Pages is static-only, so it can't run `nlr_proxy.py`. `nlr_proxy_worker.js`
+ `wrangler.toml` are a Cloudflare Worker port of the same proxy, deployed at
a static address: `https://nlr-nsrdb-proxy.plilient.workers.dev`. `index.html`'s
`PROXY_BASE_URL` const is hardcoded to that address (same pattern as
`API_KEY`) — no runtime configuration UI, since the proxy's location doesn't
vary per visitor. `.github/workflows/deploy-worker.yml` redeploys the worker
via `wrangler` whenever `nlr_proxy_worker.js` or `wrangler.toml` change on
`master` (needs the `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` repo
secrets, already configured). The `workers.dev` subdomain is fixed per
Cloudflare account, so `PROXY_BASE_URL` only needs updating if the worker is
ever renamed or moved to a custom domain.

## Architecture / data flow

1. **Dataset discovery** — calls NLR's `nsrdb_data_query.json` for the given
   lat/lon, picks the `nsrdb-GOES-aggregated-v4-0-0` dataset (broadest year
   coverage) as the primary source of real years, and separately looks for
   TMY (Typical Meteorological Year) and TGY (Typical Global Year) data.
   Those aren't folded into the primary dataset's `availableYears` — NREL
   publishes each as its own sibling dataset/output (e.g.
   `nsrdb-GOES-tmy-v4-0-0` next to `nsrdb-GOES-aggregated-v4-0-0`, or
   `nsrdb-msg-v1-0-0-tmy`/`-tgy` next to `nsrdb-msg-v1-0-0`), each with its
   own dataset name and interval — `discoverDataset()`'s
   `findSyntheticYearDataset()` scans every output in the response to find
   them. Don't hardcode a year or endpoint — NREL has retired dataset
   generations before (PSM3 → GOES-aggregated-v4) and will again.
2. **Year picker** — `pickYear()` shows a modal with that list (TGY and TMY
   first, then real years descending) and waits for the user to choose one
   before fetching. TMY/TGY have no real calendar year, so each is tracked
   internally as the literal string `'tmy'`/`'tgy'` rather than a number —
   `formatYearLabel()` is the one place that turns those into the "TMY"/"TGY"
   labels shown in the UI/report; everywhere else that branches on year
   (leap-day calc in `simulate()` and the calendar renderers) calls
   `isSyntheticYear()` rather than assuming a number. When TMY/TGY is picked,
   the fetch uses *that* sibling dataset's own name and interval, not the
   primary dataset's.
3. **Hourly GHI fetch** — downloads one year (or TMY/TGY) of hourly GHI as
   CSV for that dataset/year, through the local proxy.
4. **Simulation** (`simulate()` in the `<script>`) — walks all 8760(ish) hours,
   tracking battery state of charge, and classifies each day as a success or
   a failure based on whether the unmet fraction of that day's total
   (day+night) load exceeds the user-set "acceptable unmet energy" tolerance
   (`unmet-tolerance` input, default 10%). Runs once per PV-size ×
   battery-size combination (the cross product of the two comma-delimited
   size-list inputs).
5. **Rendering** — a comparison table, two PV×battery matrices (not-met days,
   total cost), a cost-vs-reliability scatter plot, and a detail view
   (calendar/monthly/verdict) for whichever config is currently selected.
   Selection state (`activeCfgIdx`) is shared across all of these — clicking
   any row/cell/dot re-renders all of them to stay in sync.

## Simulation model — known simplifications

These are deliberate simplifications, not bugs, but worth knowing before
trusting the numbers for a real installation:

- **GHI is used directly as PV output**, scaled by nameplate wattage and a
  flat 80% derate factor. There's no plane-of-array (POA) transposition — no
  tilt/azimuth input, and only `ghi` is even fetched from NSRDB (not
  `dni`/`dhi`, which would be needed to do POA properly). This likely
  understates winter production versus a real tilted array.
- **Battery discharge has no power/rate limit** — a battery can deliver its
  entire remaining stored energy in a single hour if the load demands it.
  There's no C-rate or inverter continuous-power cap.
- **Load is smoothed, not instantaneous** — `dayWh`/`nightWh` are spread
  evenly across all daylight/night hours of each day. There used to be a
  "peak power" input meant to flag real spikes, but it was never wired into
  the simulation (only into a warning sentence) and has been removed.
- Round-trip battery efficiency is a flat `√0.95` split evenly across charge
  and discharge; minimum state of charge is a flat 10%.

## Gotchas

- **API domain**: NREL's `developer.nrel.gov` was retired; use
  `developer.nlr.gov`. `nlr_proxy.py` only forwards to `.nlr.gov`.
- **Windows console encoding**: `nlr_proxy.py` forces UTF-8 stdout/stderr —
  without it, the proxy crashes on startup printing its banner (cp1252 can't
  encode the box-drawing characters).
- The NLR API key is hardcoded in `index.html` (`API_KEY` const). Not a secret
  in the traditional sense, but the repo (and now the hosted page) is public,
  so anyone can see and use it — keep an eye on rate-limit/quota errors from
  drive-by traffic once Pages hosting is live.
- `.claude/settings.local.json` is gitignored — it's Claude Code's local
  permission cache, not app config.
