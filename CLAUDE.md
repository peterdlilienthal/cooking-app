# Off-Grid Solar Simulator

A browser-based tool for sizing an off-grid solar + battery system. It pulls a
real year of hourly PV output from PVGIS (European Commission JRC), simulates
battery state hour-by-hour against a user-defined load, and compares many
PV/battery size combinations side by side (reliability and cost).

> **Branch note:** the `pvgis` branch (this one) has migrated the data source
> from NREL's NSRDB (GHI only, flat-panel assumption) to PVGIS `seriescalc`,
> which returns modelled hourly AC output at the optimal fixed tilt. `master`
> still runs the NSRDB version — GitHub Pages only serves `master`, so nothing
> deploys until this is merged. Before merging, `nlr_proxy_worker.js` needs the
> PVGIS host added to its allow-list (the local `nlr_proxy.py` already has it).

## Files

- **`index.html`** — the entire app: UI, PVGIS fetch logic, the hour-by-hour
  simulation, and all rendering. Single file, no build step, no external JS
  dependencies (only a Google Fonts CSS import). Named `index.html` (not
  `solar_sim_nrel.html`) so GitHub Pages serves it at the site root.
- **`nlr_proxy.py`** — a stdlib-only local CORS proxy (`http://localhost:8765`)
  that forwards browser requests to the upstream solar-data APIs (an
  `ALLOWED_HOSTS` set — PVGIS's `re.jrc.ec.europa.eu`, plus `developer.nlr.gov`
  still). Required because neither upstream sends CORS headers, so the page
  can't call them directly. Used for local dev; the hosted GitHub Pages copy
  needs a public proxy instead — see Hosting below. (Filename is still
  `nlr_proxy.py` for now — not renamed to avoid churn in launch configs.)

## Running it locally

```bash
python nlr_proxy.py          # start the proxy (leave running)
```
Then open `index.html` directly in a browser (`file://` works fine — the
proxy is what needs a real HTTP server, not the page itself). `index.html`
picks `PROXY_BASE_URL` by hostname: `plilient.github.io` → the Cloudflare
Worker, anything else (`file://`, `localhost`) → `http://localhost:8765`.

There's also a `.claude/launch.json` `app` config (`python -m http.server
8000`) for driving the page from a real `http://localhost` origin — needed
by the Claude Code Browser pane, which renders a bare `file://` as a
sandboxed `data:` snapshot that can't reach `localhost`.

## Hosting (GitHub Pages)

The repo is served via GitHub Pages (Settings → Pages → Deploy from branch
`master` / root) — every push to `master` redeploys automatically, no build
step or Actions workflow involved.

GitHub Pages is static-only, so it can't run `nlr_proxy.py`. `nlr_proxy_worker.js`
+ `wrangler.toml` are a Cloudflare Worker port of the same proxy, deployed at
a static address: `https://nlr-nsrdb-proxy.plilient.workers.dev`. `index.html`'s
`PROXY_BASE_URL` const hardcodes that address for the deployed hostname — no
runtime configuration UI, since the proxy's location doesn't vary per visitor.
**The worker still only allows `.nlr.gov`** — porting the `ALLOWED_HOSTS`
change from `nlr_proxy.py` into `nlr_proxy_worker.js` is a prerequisite for
merging the `pvgis` branch. `.github/workflows/deploy-worker.yml` redeploys the worker
via `wrangler` whenever `nlr_proxy_worker.js` or `wrangler.toml` change on
`master` (needs the `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` repo
secrets, already configured). The `workers.dev` subdomain is fixed per
Cloudflare account, so `PROXY_BASE_URL` only needs updating if the worker is
ever renamed or moved to a custom domain.

## Architecture / data flow

1. **Year discovery** (`discoverPVGIS()`) — PVGIS has no metadata endpoint, so
   this fires a deliberately-invalid probe (`startyear=9999`) at `seriescalc`.
   PVGIS rejects it instantly (HTTP 400) with the valid span in the message
   (`"...enter an integer between 2005 and 2023."`) and transfers no data; the
   regex pulls `lo`/`hi` out. Any other message (e.g. `"Location over the
   sea"`) is surfaced to the user as-is. TMY/TGY were dropped in the migration
   — PVGIS's `tmy` endpoint returns irradiance only, no PV power.
2. **Year picker** (`pickYear()`) — modal listing real years `hi…lo`
   descending. `year` is always a plain number now. `isSyntheticYear()` /
   `formatYearLabel()` survive as vestigial pass-throughs (return `false` /
   `String(year)`) so the calendar/leap-day call sites didn't all have to
   change — safe to inline and delete.
3. **Hourly PV-output fetch** (`fetchPVGIS()` → `parsePVGIS()`) — one year of
   `seriescalc` with `pvcalculation=1&peakpower=1&loss=14&optimalinclination=1`
   and `aspect` pinned equator-facing (`lat>=0 ? 0 : 180`). Returns hourly
   `{p, gi}`: `p` = AC watts for a 1 kWp array (PVGIS applies its own
   temperature + 14% loss model), `gi` = plane-of-array irradiance used only to
   separate daylight hours from night. `optimalinclination` (tilt only, robust)
   is used deliberately instead of `optimalangles` (tilt+azimuth) — the latter
   diverges to nonsense angles at some locations (Nairobi, Cape Town). PVGIS
   `aspect` is literal geographic south=0, *not* auto-hemisphere. Series is
   UTC; `toLocalTime()` rotates it by `round(lon/15)` h so `simulate()`'s
   per-day daylight/night split lines up with a real local day.
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

- **PV output comes straight from PVGIS** at the optimal fixed tilt PVGIS
  computes, azimuth forced equator-facing, with PVGIS's own cell-temperature
  and 14% system-loss model. The app does no PV modelling itself — no separate
  derate, no POA math. Azimuth isn't optimised (equator-facing is the optimum
  for a fixed array absent terrain shading or a morning/evening-skewed load,
  neither of which this simulator models). Soiling beyond PVGIS's default 14%
  (e.g. Saharan/Sahel dust) is not modelled — a candidate future input.
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

- **PVGIS API version**: use `v5_3` (`/api/v5_3/seriescalc`) — it serves
  SARAH3, 2005–2023. `v5_2` only goes to 2020. Don't hardcode the year span;
  `discoverPVGIS()` reads it from the API each time.
- **PVGIS needs no API key** — the old NSRDB `API_KEY`/`NLR_EMAIL` consts are
  gone. Nothing to leak on the public page anymore.
- **PVGIS is UTC**; its `localtime` param is silently ignored by `seriescalc`.
  `toLocalTime()` compensates by longitude. If a future change needs true
  political-timezone alignment, that's a bigger job.
- **`nlr_proxy.py` is threaded** (`ThreadingHTTPServer`) — the page fires the
  year probe and the year download nearly back to back, and a single-threaded
  server wedges every later request behind a slow upstream response.
- **Windows console encoding**: `nlr_proxy.py` forces UTF-8 stdout/stderr —
  without it, the proxy crashes on startup printing its banner (cp1252 can't
  encode the box-drawing characters).
- **NREL domain** (legacy NSRDB path, still in `ALLOWED_HOSTS`): NREL's
  `developer.nrel.gov` was retired; the NSRDB API is `developer.nlr.gov`.
- `.claude/settings.local.json` is gitignored — it's Claude Code's local
  permission cache, not app config.
