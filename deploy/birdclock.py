#!/usr/bin/env python3
"""
Bird Clock - Main Script
Plays a different bird species every hour from 7am to 9pm, cycling through
all of that species' recorded clips back to back.
Each day, 15 species are drawn at random from the full birdsongs/ pool.
Run this script on startup using cron or systemd.
"""

import os
import random
import time
import datetime
import subprocess
import json
from collections import defaultdict

from bird_names import species_slug_from_filename, bird_name_from_filename

# ── Configuration ──────────────────────────────────────────────────────────────

# Path to the flat folder of bird song clips on the SD card
SONGS_DIR = "/home/birdclock/birdclock/birdsongs"

# Active hours (inclusive) — 7am to 9pm
START_HOUR = 7
END_HOUR = 21  # 9pm in 24hr time

# How many species to feature per day (one per active hour)
SPECIES_PER_DAY = 15

# File to store today's schedule so the web page can read it
SCHEDULE_FILE = "/home/birdclock/birdclock_schedule.json"

# Where the current volume/mute state (set via the web UI) is persisted
VOLUME_FILE = "/home/birdclock/birdclock_volume.json"

# Default volume as a percentage (mpg123 default is 100, can go above)
DEFAULT_VOLUME = 80


def get_current_volume():
    """Read the current volume/mute state set via the web UI, falling back to the default."""
    if not os.path.exists(VOLUME_FILE):
        return DEFAULT_VOLUME
    try:
        with open(VOLUME_FILE, "r") as f:
            data = json.load(f)
        if data.get("muted"):
            return 0
        return int(data.get("volume", DEFAULT_VOLUME))
    except Exception:
        return DEFAULT_VOLUME

# ── Core functions ─────────────────────────────────────────────────────────────

def get_species_clips(folder):
    """Group every mp3 in folder by species, e.g. {'bewicks_wren': ['bewicks_wren_1.mp3', ...]}."""
    if not os.path.exists(folder):
        print(f"Warning: folder not found: {folder}")
        return {}
    clips_by_species = defaultdict(list)
    for f in os.listdir(folder):
        if f.endswith(".mp3"):
            clips_by_species[species_slug_from_filename(f)].append(f)
    for clips in clips_by_species.values():
        clips.sort()
    if len(clips_by_species) < SPECIES_PER_DAY:
        print(f"Warning: expected at least {SPECIES_PER_DAY} species, "
              f"found {len(clips_by_species)} in {folder}")
    return dict(clips_by_species)


def build_daily_schedule(clips_by_species):
    """
    Pick SPECIES_PER_DAY species at random and assign one to each active hour.
    Returns a dict mapping hour (int) to a list of that species' clip filenames.
    """
    species = list(clips_by_species.keys())
    random.shuffle(species)
    chosen = species[:SPECIES_PER_DAY]
    hours = list(range(START_HOUR, END_HOUR + 1))  # 7 through 21 inclusive = 15 hours
    schedule = {}
    for i, hour in enumerate(hours):
        schedule[hour] = clips_by_species[chosen[i]] if i < len(chosen) else None
    return schedule


def save_schedule(schedule, folder):
    """Save today's schedule to a JSON file so the web server can read it."""
    today = datetime.date.today().isoformat()
    data = {
        "date": today,
        "folder": folder,
        "schedule": {str(k): v for k, v in schedule.items()}
    }
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Schedule saved for {today}")


def load_schedule():
    """Load today's schedule from the JSON file. Returns None if not found or outdated."""
    if not os.path.exists(SCHEDULE_FILE):
        return None
    with open(SCHEDULE_FILE, "r") as f:
        data = json.load(f)
    today = datetime.date.today().isoformat()
    if data.get("date") != today:
        return None  # Schedule is from a previous day
    schedule = {int(k): v for k, v in data["schedule"].items()}
    return schedule, data["folder"]


def play_species(folder, filenames):
    """Play every clip for a species back to back, at a boosted volume."""
    bird_name = bird_name_from_filename(filenames[0])
    print(f"Playing: {bird_name} ({len(filenames)} clip(s))")
    for filename in filenames:
        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath):
            print(f"Error: file not found: {filepath}")
            continue
        subprocess.run([
            "mpg123", "-o", "alsa", "-a", "hw:1,0",
            "--gain", str(get_current_volume()),
            "-q", filepath
        ])


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    print("Bird Clock starting...")
    schedule = None
    folder = SONGS_DIR
    last_played_hour = -1

    while True:
        now = datetime.datetime.now()

        # Build or rebuild the schedule at midnight or on first run
        if schedule is None or now.hour == 0 and now.minute == 0 and now.second < 5:
            clips_by_species = get_species_clips(folder)
            if clips_by_species:
                schedule = build_daily_schedule(clips_by_species)
                save_schedule(schedule, folder)
                last_played_hour = -1  # Reset so midnight hour can play if active
                print(f"New schedule built for {now.strftime('%B %d, %Y')}")
            else:
                print("No songs found — check your birdsongs folder.")
                time.sleep(60)
                continue

        # Check if it's the top of an active hour and we haven't played yet this hour
        if (now.minute == 0 and
            now.second < 10 and
            now.hour != last_played_hour and
            START_HOUR <= now.hour <= END_HOUR):

            filenames = schedule.get(now.hour)
            if filenames:
                play_species(folder, filenames)
                last_played_hour = now.hour

        # Sleep for a few seconds before checking again
        time.sleep(5)


if __name__ == "__main__":
    main()
