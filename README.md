# birdclock

A bird-song learning clock: a Raspberry Pi plays a different local bird
species every hour, cycling through all of that species' recorded clips
back to back, and shows what's currently playing on a small web page.

The project has two halves:

- **Collection** (`collect/`) — a CLI tool that fetches bird song
  recordings from [Xeno-canto](https://xeno-canto.org), cleans them up,
  and builds short looping mp3 clips straight into `birdsongs/`. Runs on
  a dev machine.
- **Deployment** (`deploy/`) — the code that actually runs on the
  Raspberry Pi: each day it picks 15 species at random from `birdsongs/`,
  one per active hour, and serves the status page.

```
collect/           fetch + clean up recordings           (dev machine)
species_list.txt   the ~100 species to fetch
birdsongs/         flat pool of clips, numbered per species
deploy/            playback + web status page             (Raspberry Pi)
```

## Collecting songs

For each species, the fetch pipeline builds `--candidates` clips (3 by
default) — each one an independent recording of that species, not just a
top pick:

1. Searches Xeno-canto for high-quality, song-type recordings and ranks
   candidates by how likely they are to be a clean, isolated song (exact
   `song` tag, few background species, sane recording length).
2. Downloads a candidate and measures its actual signal-to-noise ratio;
   if it's too noisy, tries the next-ranked candidate instead.
3. Trims the recording down to a single song, discarding leading/trailing
   dead air.
4. Normalizes volume and builds a ~30 second clip that loops the song every
   5-10 seconds, with the gaps filled by real ambience captured from the
   recording itself (falling back to synthetic ambient noise if a
   recording doesn't have enough usable dead air).
5. Writes the result to `birdsongs/<species>_<n>.mp3`, along with an
   attribution sidecar file (Xeno-canto recordings are Creative Commons
   licensed and require attribution).

### Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org) (used by `pydub` for audio encode/decode)
- A free [Xeno-canto API key](https://xeno-canto.org/account)

### Installation

**Install ffmpeg**

macOS: `brew install ffmpeg`
Windows: `winget install ffmpeg`
Linux (Debian/Ubuntu): `sudo apt install ffmpeg`
Other Linux distributions: install `ffmpeg` via your package manager
(`dnf`, `pacman`, etc.).

**Install the project**

Using [uv](https://docs.astral.sh/uv/) (recommended):

```sh
uv sync
```

Using `pip` in a virtual environment:

```sh
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

**Configure your API key**

```sh
cp .env.example .env
# edit .env and paste in your key from https://xeno-canto.org/account
```

### Usage

If you installed with uv, prefix commands with `uv run`. With a `pip`
install, activate your virtualenv first and drop the `uv run` prefix.

```sh
# fetch specific species
uv run birdclock-songs fetch "American Robin" "Black-capped Chickadee"

# fetch everything listed in species_list.txt
uv run birdclock-songs fetch --from-file species_list.txt

# both at once, and re-fetch even if already downloaded
uv run birdclock-songs fetch "Blue Jay" --from-file species_list.txt --force
```

| Flag | Description |
| --- | --- |
| `--from-file <path>` | Read species names (one per line) from a text file. Lines starting with `#` are ignored. |
| `--output-dir <path>` | Where to write clips (default: `birdsongs/`). |
| `--force` | Re-process a species even if its clips already exist. |
| `--candidates <n>` | Number of independent clips to build per species (default: `3`). |

### Output

Each species produces `<n>` pairs of files directly in `birdsongs/`:

- `<species_slug>_<n>.mp3` — a looping clip
- `<species_slug>_<n>.attribution.txt` — recordist, license, and source URL

`<species_slug>` has apostrophes dropped entirely rather than turned into
an underscore (e.g. `"Bewick's Wren"` → `bewicks_wren`), so `deploy/`
can cleanly restore the display name later. Species that already have all
`<n>` clips are skipped on subsequent runs unless `--force` is passed.

## Deploying to the Pi

`birdsongs/` is a flat pool of every species' clips — no seasonal
curation, no month folders. The code that runs on the Pi itself lives in
`deploy/`:

- **`birdclock.py`** — the main loop. Each day it groups `birdsongs/` by
  species, picks 15 at random, and assigns one per active hour (7am–9pm
  by default). When a species' hour comes up, all of its clips play back
  to back through `mpg123` — that's the hourly play unit, not a single
  clip. Writes the day's schedule to `birdclock_schedule.json` so the web
  server can read it.
- **`birdclock_web.py`** — a small Flask app that reads that schedule
  file and shows the current hour's species (name + Wikipedia photos) on
  a local status page.
- **`bird_names.py`** — shared helper that turns a clip's filename into a
  species slug and display name, restoring apostrophes that were dropped
  for filesystem safety (`bewicks_wren_2.mp3` → `"Bewick's Wren"`). Both
  scripts above import it. This matters most for `birdclock_web.py`: the
  display name is also the exact string sent to Wikipedia's REST API,
  which does no fuzzy matching, so a missing apostrophe can silently
  break a photo lookup.
- **`i2samp.py`** — Adafruit's I2S amp setup script, for initial hardware
  bring-up on the Pi.

Sync `deploy/*.py` and `birdsongs/` to the Pi (e.g. `rsync`). All the
`deploy/` Python files need to stay in the same directory there, since
`bird_names.py` is imported by relative path rather than installed as a
package. Run `birdclock.py` on boot (cron or systemd), and `birdclock_web.py`
alongside it to serve the status page at `http://<pi-hostname>.local:5000`.

## License

Generated audio clips are derived from Xeno-canto recordings and remain
subject to each recording's individual Creative Commons license, noted in
its attribution file.
