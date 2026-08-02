# Off-Grid Solar Simulator

A browser-based tool for sizing an off-grid solar + battery system. It pulls a
real year of hourly solar irradiance from NREL's NSRDB, simulates battery
state hour-by-hour against a user-defined load, and compares many PV/battery
size combinations side by side (reliability and cost).

## Files

- **`solar_sim_nrel.html`** — the entire app: UI, NSRDB fetch logic, the
  hour-by-hour simulation, and all rendering. Single file, no build step, no
  external JS dependencies (only a Google Fonts CSS import).
- **`nlr_proxy.py`** — a stdlib-only local CORS proxy (`http://localhost:8765`)
  that forwards browser requests to NREL's API. Required because NREL doesn't
  send CORS headers, so the page can't call it directly.

## Running it

```bash
python nlr_proxy.py          # start the proxy (leave running)
```
Then open `solar_sim_nrel.html` directly in a browser (`file://` works fine —
the proxy is what needs a real HTTP server, not the page itself).

## Architecture / data flow

1. **Dataset discovery** — calls NLR's `nsrdb_data_query.json` for the given
   lat/lon, picks the `nsrdb-GOES-aggregated-v4-0-0` dataset (broadest year
   coverage) and its most recent available year. Don't hardcode a year or
   endpoint — NREL has retired dataset generations before (PSM3 →
   GOES-aggregated-v4) and will again.
2. **Hourly GHI fetch** — downloads one year of hourly GHI as CSV for that
   dataset/year, through the local proxy.
3. **Simulation** (`simulate()` in the `<script>`) — walks all 8760(ish) hours,
   tracking battery state of charge, and classifies each day as
   full/partial/failed based on what fraction of that day's total (day+night)
   load was served. Runs once per PV-size × battery-size combination (the
   cross product of the two comma-delimited size-list inputs).
4. **Rendering** — a comparison table, two PV×battery matrices (not-met days,
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
  `developer.nlr.gov`. `nlr_proxy.py` tries `.nlr.gov` first and falls back to
  `.nrel.gov`.
- **Windows console encoding**: `nlr_proxy.py` forces UTF-8 stdout/stderr —
  without it, the proxy crashes on startup printing its banner (cp1252 can't
  encode the box-drawing characters).
- The NLR API key is hardcoded in `solar_sim_nrel.html` (`API_KEY` const). Not
  a secret in the traditional sense, but don't casually publish this repo
  publicly without swapping it out.
- `.claude/settings.local.json` is gitignored — it's Claude Code's local
  permission cache, not app config.
