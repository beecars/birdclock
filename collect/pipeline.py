"""Orchestrates fetching, trimming, normalizing, and looping a species' song."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from pydub import AudioSegment

from . import audio, xeno_canto

OUTPUT_DIR = Path("birdsongs")
MAX_CANDIDATE_ATTEMPTS = 10


def filename_slug(species: str) -> str:
    """Convert a species name into a playback filename slug, apostrophes dropped entirely
    (rather than turned into an underscore) so it round-trips back to a clean display name
    via deploy/bird_names.py.

    Args:
        species (str): The species common name, e.g. "Bewick's Wren".

    Returns:
        str: A slug suitable for use in filenames, e.g. "bewicks_wren".
    """
    slug = species.strip().lower().replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def _write_attribution(path: Path, species: str, recording: xeno_canto.Recording) -> None:
    """Write attribution information for a recording to a text file.

    Args:
        path (Path): Path to the attribution file to write.
        species (str): The species common name, e.g. "American Robin".
        recording (xeno_canto.Recording): The recording object containing metadata.
    """
    path.write_text(
        f"Species: {species}\n"
        f"Recordist: {recording.recordist}\n"
        f"Country: {recording.country}\n"
        f"Quality: {recording.quality}\n"
        f"Type: {recording.recording_type}\n"
        f"License: {recording.license_url}\n"
        f"Source: {recording.page_url}\n"
    )


def process_species(
    species: str,
    output_dir: Path = OUTPUT_DIR,
    force: bool = False,
    num_candidates: int = 3,
) -> list[Path]:
    """Fetch, clean, and loop up to num_candidates recordings for a species.

    Every candidate clip for a species is an independent hourly play unit on the clock --
    deploy/birdclock.py plays all of a species' clips back to back during its hour -- so output
    is always flat, numbered per species: <output_dir>/<slug>_1.mp3, <slug>_2.mp3, ...

    Args:
        species (str): The species common name, e.g. "American Robin".
        output_dir (Path): Base directory to write output files into.
        force (bool): If True, reprocess and overwrite even if output already exists.
        num_candidates (int): Number of candidate clips to produce for the species.

    Returns:
        list[Path]: Paths to the mp3 files written, in candidate order.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = filename_slug(species)

    out_paths = [output_dir / f"{slug}_{i}.mp3" for i in range(1, num_candidates + 1)]
    attribution_paths = [
        output_dir / f"{slug}_{i}.attribution.txt" for i in range(1, num_candidates + 1)
    ]

    # Skip species already fully processed, unless the caller asked to redo them.
    if not force and all(p.exists() for p in out_paths):
        print(f"skip {species!r}: output already exists in {output_dir} (use --force to redo)")
        return out_paths

    print(f"searching for {species!r}...")
    ranked = xeno_canto.rank(species)

    # Candidates that cleared the noise floor, in the order they were found.
    winners: list[tuple[xeno_canto.Recording, AudioSegment, AudioSegment]] = []
    # Candidates that didn't clear the bar, kept in case there aren't enough winners to fill
    # num_candidates -- best (least noisy) first.
    fallbacks: list[tuple[float, tuple]] = []

    with tempfile.TemporaryDirectory() as tmp:
        # Work through ranked candidates in order, stopping once enough winners are found.
        for i, recording in enumerate(ranked[:MAX_CANDIDATE_ATTEMPTS], start=1):
            if len(winners) >= num_candidates:
                break

            raw_path = Path(tmp) / f"{slug}_raw_{i}"
            print(f"  downloading recording {recording.id} ({recording.length_seconds:.0f}s, "
                  f"quality {recording.quality}, type {recording.recording_type!r})...")
            xeno_canto.download(recording, raw_path)

            # Normalize volume before measuring SNR so loudness differences between recordings don't
            # skew the noise-floor comparison.
            clip = AudioSegment.from_file(raw_path)
            clip = audio.normalize_volume(clip)
            snr = audio.estimate_snr_db(clip)
            song, ambience = audio.extract_song_and_ambience(clip)
            snr_display = f"{snr:.1f}dB" if snr is not None else "unmeasurable (clip too short)"
            print(f"    estimated SNR: {snr_display}")

            # Candidates below the noise floor become fallbacks instead of winners, but stay
            # available in case too few candidates clear the bar.
            result = (recording, song, ambience)
            if snr is not None and snr < audio.MIN_SNR_DB:
                print(f"    noise floor too high (SNR ~{snr:.1f}dB), trying next candidate...")
                fallbacks.append((snr, result))
                continue

            winners.append(result)

        # If not enough candidates cleared the noise floor, backfill with the least-noisy fallbacks
        # rather than returning fewer than requested.
        if len(winners) < num_candidates:
            needed = num_candidates - len(winners)
            fallbacks.sort(key=lambda item: item[0] if item[0] is not None else float("-inf"), reverse=True)
            if fallbacks:
                print(f"    warning: only {len(winners)}/{num_candidates} candidates cleared "
                      "the noise floor check; filling remaining slots with the next-cleanest")
            winners.extend(result for _, result in fallbacks[:needed])

        # Even with fallbacks, there may not be enough usable candidates at all.
        if len(winners) < num_candidates:
            print(f"    warning: only found {len(winners)}/{num_candidates} usable candidates "
                  f"for {species!r}")

        # Build and export the final loop for each winning candidate.
        written: list[Path] = []
        for idx, (recording, song, ambience) in enumerate(winners[:num_candidates]):
            looped = audio.build_loop(song, ambience)
            looped.export(out_paths[idx], format="mp3", bitrate="128k")
            _write_attribution(attribution_paths[idx], species, recording)
            print(f"  wrote {out_paths[idx]} ({len(looped) / 1000:.1f}s)")
            written.append(out_paths[idx])

    return written
