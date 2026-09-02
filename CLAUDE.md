# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI tool that fetches bird song recordings from the Xeno-canto API, cleans
them up, and builds short looping mp3 clips for a bird-song learning clock
(a Raspberry Pi project — see `description` in pyproject.toml). The repo
covers both halves of that project:

- **Preprocessing** (`collect/`) — runs on a dev machine, fetches and
  cleans up candidate recordings, writing finished clips straight into
  `birdsongs/`.
- **Deployment** (`deploy/`) — runs on the Raspberry Pi itself: picks
  species at random from `birdsongs/` on a daily schedule and serves a
  small status web page.

`birdsongs/` is a flat pool of clips synced to the Pi's `~/birdclock/birdsongs`
(the repo is cloned onto the Pi at `~/birdclock`) — no seasonal curation,
no month folders. Each species gets `--candidates`
independent clips (3 by default), and on the Pi, a species' hour plays
*all* of its clips back to back — the hourly play unit is a species, not
a single clip.

## Setup and commands

```sh
uv sync                     # install dependencies into .venv
uv run birdclock-songs fetch "American Robin" "Black-capped Chickadee"
uv run birdclock-songs fetch --from-file species_list.txt
uv run birdclock-songs fetch "Blue Jay" --from-file species_list.txt --force
```

Requires `ffmpeg` on PATH (used by `pydub`) and a Xeno-canto API key set as
`XENO_CANTO_API_KEY` in a `.env` file at the repo root (loaded via
`python-dotenv`); copy `.env.example` to `.env` and fill in the key.

There is no test suite, linter, or formatter configured in this project.

## Architecture

Pipeline, one species at a time, orchestrated by `process_species()` in
`collect/pipeline.py`:

1. **`xeno_canto.py`** — talks to the Xeno-canto v3 API. `rank()` searches
   for a species (quality "A", falling back to "B"), then sorts candidates
   by a scoring tuple in `_score()`: exact `song`-type tag first, then
   fewest background species, then a sane length range, then closeness to
   a 30s target. Returns a ranked list (not just one pick), so the
   pipeline can fall through to the next candidate.

2. **`pipeline.py`** — `process_species()` downloads candidates in ranked
   order, computes each one's SNR via `audio.estimate_snr_db()`, and keeps
   every one that clears `audio.MIN_SNR_DB`, up to `num_candidates` (default
   3) — these are independent hourly play units, not alternates competing
   for a single winner slot. If too few clear the bar, it backfills with
   the least-noisy fallbacks rather than returning fewer than requested.
   Writes `birdsongs/<slug>_<n>.mp3` plus a matching
   `birdsongs/<slug>_<n>.attribution.txt` sidecar (Xeno-canto recordings
   are CC-licensed and require attribution), where `<slug>` comes from
   `filename_slug()` — apostrophes dropped entirely (`"Bewick's Wren"` →
   `bewicks_wren`), so `deploy/bird_names.py` can cleanly restore the
   display name later. Skips species whose `<n>` clips already exist
   unless `force=True`.

3. **`audio.py`** — signal processing, independent of the network layer:
   - `extract_song_and_ambience()` splits a recording into its first clean
     song plus the surrounding dead air (lead-in + trailing silence up to
     the next detected song), using `pydub.silence.detect_nonsilent`. The
     dead air becomes real "room tone" for gap-filling later, rather than
     synthetic silence.
   - `estimate_snr_db()` measures noise independently of silence
     detection — it compares the 95th vs 15th percentile of per-frame RMS
     loudness across the whole clip, so it still works on recordings whose
     noise floor never drops low enough to register as "silence".
   - `build_loop()` repeats the extracted song every `GAP_MS`, filling
     gaps with the real ambience bed (tiled/crossfaded to length), falling
     back to generated brown noise (`_generate_noise()`) when a recording
     doesn't have enough usable dead air. Clip length targets `LOOP_TARGET_MS`
     (30s) but extends up to `LOOP_MAX_MS` (60s) to guarantee at least one
     repeat for long songs.

4. **`cli.py`** — thin argparse wrapper (`fetch` subcommand) that resolves
   the species list (positional args + `--from-file`) and calls
   `process_species()` per species, catching and logging per-species
   errors so one failure doesn't abort a batch run.

`species_list.txt` is a working data file (not code): 100 species common
to the Pacific Northwest, one common name per line, `#`-comments allowed.

## Deployment (`deploy/`)

Code that actually runs on the Raspberry Pi, independent of the fetch
pipeline above — it only reads finished mp3s, it doesn't know about
Xeno-canto:

- **`birdclock.py`** — main loop. Each day (and on startup) it calls
  `get_species_clips()` to group every file in `birdsongs/` by species
  (via `bird_names.species_slug_from_filename()`), picks `SPECIES_PER_DAY`
  (15) at random, and assigns one species per active hour
  (`START_HOUR`–`END_HOUR`). When a species' hour comes up, `play_species()`
  plays every one of its clips back to back through `mpg123`. Writes the
  day's schedule (hour → list of filenames) to `birdclock_schedule.json`
  so the web server can read it.
- **`birdclock_web.py`** — small Flask app that reads that schedule file
  and shows the current hour's species (name + Wikipedia photos) on a local
  web page. Uses the first filename in the hour's clip list to derive the
  display name.
- **`bird_names.py`** — shared helpers. `species_slug_from_filename()`
  strips the trailing candidate index (`bewicks_wren_2.mp3` →
  `bewicks_wren`) so `birdclock.py` can group clips by species.
  `bird_name_from_filename()` turns that slug into a display name,
  restoring apostrophes that were dropped for filesystem safety
  (`bewicks_wren` → `"Bewick's Wren"`) via the `APOSTROPHE_NAMES` map —
  keep it in sync with whatever apostrophe'd species actually appear in
  `birdsongs/`. This matters most for `birdclock_web.py`: the display name
  is also the exact string sent to Wikipedia's REST API, which does no
  fuzzy matching, so a missing apostrophe can silently break a photo
  lookup.
- **`i2samp.py`** — Adafruit's I2S amp setup script, for initial hardware
  bring-up on the Pi.
- **`start_birdclock.sh`** — launches `birdclock.py` and `birdclock_web.py`
  together in the background; this is what the boot cron job runs.
- All `deploy/` Python files must stay in the same directory on the Pi
  (`bird_names.py` is imported by relative path, not installed as a
  package).
- Flask is declared as the `deploy` optional-dependency group in
  `pyproject.toml` (`pip install ".[deploy]"`), since `deploy/` has no
  other third-party dependencies and doesn't need `collect/`'s.

### How the Pi actually runs this

On the deployed Pi (see `README.md`'s "Deploying to the Pi" section for
full setup steps): the repo is cloned to `~/birdclock`, `deploy/`'s two
long-running scripts run under the Pi's system Python (no venv), and a
user crontab `@reboot` entry runs `deploy/start_birdclock.sh` on boot —
there's no systemd unit. `birdclock.py` and `birdclock_web.py` hardcode
absolute paths (`SONGS_DIR`, `SCHEDULE_FILE`) rather than deriving them
from `__file__`, so cloning to a different location or running as a
different user requires updating those constants by hand. The Pi has
local mDNS (`birdclock.local`) and internet access, so `git pull` on the
Pi works for pulling code changes.
