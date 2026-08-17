"""Search and download bird recordings from the Xeno-canto API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://xeno-canto.org/api/3/recordings"


@dataclass
class Recording:
    id: str
    english_name: str
    recordist: str
    country: str
    quality: str
    recording_type: str
    background_species: list[str]
    length_seconds: float
    file_url: str
    license_url: str
    page_url: str


class NoRecordingsFoundError(RuntimeError):
    pass


def _api_key() -> str:
    """Read the Xeno-canto API key from the environment.

    Returns:
        The API key.
    """
    key = os.environ.get("XENO_CANTO_API_KEY")
    # Fail fast with setup instructions rather than letting requests error out obscurely later.
    if not key:
        raise RuntimeError(
            "XENO_CANTO_API_KEY is not set. Get a free key at "
            "https://xeno-canto.org/account and export it."
        )
    return key


def _parse_length(length: str) -> float:
    """Parse a Xeno-canto length string into seconds.

    Args:
        length: Duration as "M:SS" or "H:MM:SS".

    Returns:
        The duration in seconds.
    """
    # Xeno-canto reports length as "M:SS" or "H:MM:SS".
    parts = [float(p) for p in length.split(":")]
    # Fold parts left-to-right (base 60) so this works for both "M:SS" and "H:MM:SS".
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _absolute_url(url: str) -> str:
    """Turn a Xeno-canto protocol-relative URL into an absolute https URL.

    Args:
        url: A URL as returned by the API, possibly starting with "//".

    Returns:
        An absolute URL.
    """
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _to_recording(raw: dict) -> Recording:
    """Convert a raw Xeno-canto API recording entry into a Recording.

    Args:
        raw: The recording's raw JSON dict from the API response.

    Returns:
        The parsed Recording.
    """
    return Recording(
        id=raw["id"],
        english_name=raw.get("en", ""),
        recordist=raw.get("rec", "unknown"),
        country=raw.get("cnt", ""),
        quality=raw.get("q", ""),
        recording_type=raw.get("type", ""),
        background_species=raw.get("also", []),
        length_seconds=_parse_length(raw.get("length", "0:00")),
        file_url=_absolute_url(raw["file"]),
        license_url=_absolute_url(raw.get("lic", "")),
        page_url=_absolute_url(raw.get("url", "")),
    )


# Xeno-canto's `en:` field is an exact match against whatever English name is in *its*
# database, which sometimes diverges from the eBird/AOS name in species_list.txt -- either
# just a missing hyphen in a compound group name ("Western Screech-Owl" -> "Western Screech
# Owl"), or a genuinely different name for the same species (older/regional common name,
# British spelling). Only species that hit this are listed; if a fresh species_list.txt
# entry comes up with zero results, check whether it's one of these two cases before
# assuming the species has no recordings at all.
XC_NAME_OVERRIDES = {
    "Yellow-rumped Warbler": "Myrtle Warbler",
    "Black-throated Gray Warbler": "Black-throated Grey Warbler",
    "Common Raven": "Northern Raven",
    "European Starling": "Common Starling",
}


def _name_variants(species: str) -> list[str]:
    """Candidate English names to try against Xeno-canto's exact en: match, in order."""
    variants = [species]
    no_hyphen = species.replace("-", " ")
    if no_hyphen != species:
        variants.append(no_hyphen)
    override = XC_NAME_OVERRIDES.get(species)
    if override and override not in variants:
        variants.append(override)
    return variants


def search(species: str, quality: str = "A") -> list[Recording]:
    """Search Xeno-canto for a species at a given minimum quality rating.

    Not restricted to type:song at the API level -- some species (owls,
    ravens, starlings, etc.) have little or nothing tagged exactly "song"
    on Xeno-canto, and a hard filter here means those species return zero
    results and fail outright. _score() still strongly prefers exact
    "song"-tagged recordings when ranking candidates, so this just widens
    the pool instead of hard-excluding species that lack that tag.

    Tries a few name variants (see _name_variants()) before giving up, since
    Xeno-canto's stored English name for a species doesn't always match the
    eBird/AOS name callers pass in.

    Args:
        species: The species common name, e.g. "American Robin".
        quality: Minimum Xeno-canto quality rating to search for, e.g. "A".

    Returns:
        Matching recordings, unranked.
    """
    for name in _name_variants(species):
        params = {
            "query": f'en:"{name}" q:{quality}',
            "key": _api_key(),
        }
        response = requests.get(API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        recordings = [_to_recording(r) for r in data.get("recordings", [])]
        if recordings:
            if name != species:
                print(f"    (found under Xeno-canto's name {name!r})")
            return recordings
    return []


MIN_RECORDING_SECONDS = 5
IDEAL_LENGTH_RANGE = (5, 45)
TARGET_LENGTH_SECONDS = 30


def _score(recording: Recording) -> tuple:
    """Lower is better. Ranked by: exact-song tagging, then background-species
    contamination, then whether length falls in a sane band, then closeness to
    a 30s target length -- long enough to likely hold the complete, un-clipped
    song, short enough to stay clear of multi-vocalization field sessions.

    Args:
        recording: The recording to score.

    Returns:
        A sort key tuple; recordings compare as better the lower this sorts.
    """
    # Compute each ranking signal in the priority order described above, then combine
    # them into a single tuple so callers can sort candidates with plain sorted().
    is_exact_song = 0 if recording.recording_type.strip().lower() == "song" else 1
    background_count = len(recording.background_species)
    lo, hi = IDEAL_LENGTH_RANGE
    in_ideal_range = 0 if lo <= recording.length_seconds <= hi else 1
    distance_from_target = abs(recording.length_seconds - TARGET_LENGTH_SECONDS)
    return (is_exact_song, background_count, in_ideal_range, distance_from_target)


def rank(species: str) -> list[Recording]:
    """Find and rank candidate recordings for a species, best first.

    Prefers quality "A" recordings, falling back to "B" with a warning if
    none are available. Ranks by signals that predict a clean, isolated
    song rather than a long multi-vocalization field session: an exact
    "song" type tag (not "song, call" etc.), fewer background species
    logged by the recordist, and a sane length range -- with raw length
    only used as a last-resort tiebreaker.

    Returns a ranked list (not just one pick) so callers can fall through
    to the next candidate if the top choice turns out, on inspection of
    the actual audio, to have a high noise floor.

    Args:
        species: The species common name, e.g. "American Robin".

    Returns:
        Candidate recordings, best first.
    """
    # Try quality "A" first, only falling back to "B" if "A" has no results at all.
    for quality in ("A", "B"):
        recordings = search(species, quality=quality)
        if recordings:
            if quality != "A":
                print(f"  warning: no q:A recordings for {species!r}, using q:B")
            # Prefer recordings above the minimum length, but don't discard everything
            # if the species only has short clips available.
            candidates = [r for r in recordings if r.length_seconds >= MIN_RECORDING_SECONDS]
            pool = candidates or recordings
            return sorted(pool, key=_score)
    raise NoRecordingsFoundError(f"No recordings found for {species!r}")


def download(recording: Recording, dest: Path) -> Path:
    """Download a recording's audio file to disk.

    Args:
        recording: The recording to download.
        dest: File path to write the audio bytes to.

    Returns:
        The path the recording was written to (same as dest).
    """
    response = requests.get(recording.file_url, timeout=60)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest
