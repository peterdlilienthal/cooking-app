# Off-Grid Solar + Battery Simulator

A browser-based tool for sizing an off-grid solar + battery system. It pulls a
real year of hourly PV output from PVGIS (European Commission JRC), simulates
battery state hour-by-hour against a user-defined load, and compares many
PV/battery size combinations side by side (reliability and cost).

The data source was migrated from NREL's NSRDB (GHI only, flat-panel
assumption) to PVGIS `seriescalc`, which returns modelled hourly AC output for
a fixed array facing the equator at tilt = latitude.

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
The worker's `ALLOWED_HOSTS` must stay in sync with `nlr_proxy.py`'s (both
allow PVGIS + NLR). `.github/workflows/deploy-worker.yml` redeploys the worker
via `wrangler` whenever `nlr_proxy_worker.js` or `wrangler.toml` change on
`master` (needs the `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` repo
secrets, already configured) — so a merge that touches the worker only takes
effect on the live site once that Action finishes. The `workers.dev` subdomain
is fixed per Cloudflare account, so `PROXY_BASE_URL` only needs updating if the
worker is ever renamed or moved to a custom domain.

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
   `seriescalc` with `pvcalculation=1&peakpower=1&loss=14`, `angle=|lat|`
   (tilt = latitude rule of thumb) and `aspect` equator-facing (`lat>=0 ? 0 :
   180`). Returns hourly `{p, gi}`: `p` = AC watts for a 1 kWp array (PVGIS
   applies its own temperature + 14% loss model), `gi` = plane-of-array
   irradiance used only to separate daylight hours from night. PVGIS's tilt
   optimisers aren't used — `optimalinclination` gives near-identical yields to
   the latitude rule at these latitudes, and `optimalangles` (joint
   tilt+azimuth) diverges to nonsense angles at some locations (Nairobi, Cape
   Town). PVGIS `aspect` is literal geographic south=0, *not* auto-hemisphere.
   Series is UTC; `toLocalTime()` rotates it by `round(lon/15)` h so
   `simulate()`'s per-day daylight/night split lines up with a real local day.
4. **Simulation** (`simulate()` in the `<script>`) — walks all 8760(ish) hours,
   tracking battery state of charge, and classifies each day as a success or
   a failure based on whether the unmet fraction of that day's total
   (day+night) load exceeds the user-set "acceptable unmet energy" tolerance
   (`unmet-tolerance` input, default 10%). Runs once per PV-size ×
   battery-size combination.
   - **Search grid** (`parseRange()`) — the "System Configurations" card has
     three inputs per axis: min, max, interval (`pv-min` / `pv-max` / `pv-int`,
     `batt-min` / `batt-max` / `batt-int`). `parseRange()` validates one axis's
     trio and returns the sorted list of sizes (`min`, `min+interval`, … ≤
     `max`); `MAX_RANGE_VALUES` (120) caps each axis so the grid stays
     renderable. `performSimulation()` crosses the two lists and simulates
     every `{pv, batt}` — a plain sweep, no search heuristic. `updateComboPreview()`
     shows the combination count live and surfaces validation errors from the
     same `parseRange()`. The computed lists are still written into the hidden
     `pv-sizes` / `batt-sizes` inputs so the report reads them unchanged; the
     six range inputs are in `CONFIG_FIELD_IDS` (localStorage) and
     `SIM_TRIGGER_FIELD_IDS` (Enter-to-run).
5. **Rendering** — a comparison table, two PV×battery matrices (not-met days,
   total cost), a cost-vs-reliability scatter plot, and a detail view
   (calendar/monthly/verdict) for whichever config is currently selected.
   Selection state (`activeCfgIdx`) is shared across all of these — clicking
   any row/cell/dot re-renders all of them to stay in sync.
   - **Scatter axis truncation** — the scatter x-axis is filtered by the
     `#scatter-met-floor` input in the card header ("Show ≥ __ % load met"):
     hides configs below that "% of load met" and rescales the x-axis (and its
     `% of load met` tick labels) to end exactly at the floor; blank /
     out-of-range ⇒ no x truncation. The y-axis is *always* capped at the
     `SCATTER_RELIABLE_CAP`-th (2nd) cheapest 100%-reliable config's cost, so
     only that many reliable systems (all at unmet = 0) stay in view and the
     y-axis rescales to that band — no cap when fewer than that many configs
     reach 100%. `scatterMetFloor()` / `scatterUnmetCutoff()` /
     `scatterCostCutoff()` and the combined `inView(i)` predicate are shared by
     `renderUnmetKwhScatter()` and the Word-report canvas so the export matches
     the screen.

## Simulation model — known simplifications

These are deliberate simplifications, not bugs, but worth knowing before
trusting the numbers for a real installation:

- **PV output comes straight from PVGIS** for a fixed array facing the equator,
  tilted at an angle equal to latitude, with PVGIS's own cell-temperature and
  14% system-loss model. The app does no PV modelling itself — no separate
  derate, no POA math. Tilt = latitude is a rule of thumb, not per-site optimal
  (optimal is usually a bit shallower). Soiling beyond PVGIS's default 14%
  (e.g. Saharan/Sahel dust) is not modelled — a candidate future input.
- **Battery discharge has no power/rate limit** — a battery can deliver its
  entire remaining stored energy in a single hour if the load demands it.
  There's no C-rate or inverter continuous-power cap.
- **Load is smoothed, not instantaneous** — `dayWh`/`nightWh` are spread
  evenly across all daylight/night hours of each day. There used to be a
  "peak power" input meant to flag real spikes, but it was never wired into
  the simulation (only into a warning sentence) and has been removed.
- Round-trip battery efficiency is a flat 95% (`cEff = dEff = √0.95`, split
  evenly across charge and discharge); minimum state of charge is a flat 10%.

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
