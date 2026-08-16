"""Trim dead air, normalize volume, and build a looping clip."""

from __future__ import annotations

import numpy as np
from pydub import AudioSegment, effects, silence

MIN_SONG_MS = 300
MAX_SONG_MS = 12_000
SILENCE_MIN_LEN_MS = 3000
SILENCE_THRESH_OFFSET_DB = 16
EDGE_PADDING_MS = 100

GAP_MS = 2_000
LOOP_TARGET_MS = 30_000
LOOP_MAX_MS = 60_000
NOISE_GAIN_DB = -28.0

MIN_AMBIENCE_MS = 500
FALLBACK_NOISE_MS = 3_000
CROSSFADE_MS = 80
MIN_SNR_DB = 20.0

_NP_DTYPE = {1: np.int8, 2: np.int16, 4: np.int32}


def normalize_volume(audio: AudioSegment) -> AudioSegment:
    """Normalize a clip's peak amplitude up to 0 dBFS.

    Args:
        audio: The clip to normalize.

    Returns:
        A new AudioSegment with peak volume normalized.
    """
    return effects.normalize(audio)


def extract_song_and_ambience(audio: AudioSegment) -> tuple[AudioSegment, AudioSegment]:
    """Split a recording into its first clean song and the dead air around it.

    The dead air is real ambience from the same recording (room tone, wind, distant birds) -- used
    later as a natural-sounding bed for the gaps between repeats, instead of synthetic silence or
    noise. Combines lead-in silence (before the song starts) with any trailing silence (after it
    ends): tightly-trimmed recordings often have little to no dead air *after* the song, but
    recordists reliably leave a moment of lead-in before it.

    Args:
        audio: The full recording to split.

    Returns:
        A tuple of (song, ambience) clips.
    """
    # Detect non-silent stretches using a threshold relative to this clip's own loudness.
    silence_thresh = audio.dBFS - SILENCE_THRESH_OFFSET_DB
    segments = silence.detect_nonsilent(
        audio, min_silence_len=SILENCE_MIN_LEN_MS, silence_thresh=silence_thresh
    )
    # Discard blips too short to be a real song.
    candidates = [(s, e) for s, e in segments if (e - s) >= MIN_SONG_MS]
    if not candidates:
        # Nothing detected as silence-bounded; fall back to the whole clip.
        return audio, AudioSegment.silent(duration=0)

    # Pad the detected song boundaries so the trim doesn't clip its edges.
    song_start, song_end = candidates[0]
    padded_start = max(0, song_start - EDGE_PADDING_MS)
    detected_end = min(len(audio), song_end + EDGE_PADDING_MS)
    # Cap a merged multi-phrase block to a single-song length, without moving detected_end.
    song = audio[padded_start:min(detected_end, padded_start + MAX_SONG_MS)]

    lead_in = audio[0:padded_start]

    # Trailing ambience runs up to the next detected song (if any), otherwise to the clip's end.
    trailing_start = detected_end
    if len(candidates) > 1:
        trailing_end = max(trailing_start, candidates[1][0] - EDGE_PADDING_MS)
    else:
        trailing_end = len(audio)
    trailing = audio[trailing_start:trailing_end] if trailing_end > trailing_start else AudioSegment.silent(duration=0)

    ambience = lead_in + trailing
    return song, ambience


SNR_FRAME_MS = 50
SNR_NOISE_PERCENTILE = 15
SNR_SIGNAL_PERCENTILE = 95
MIN_SNR_FRAMES = 10


def _frame_dbfs(clip: AudioSegment, frame_ms: int = SNR_FRAME_MS) -> np.ndarray:
    """Per-frame loudness in dBFS, as a mono RMS envelope over fixed-length frames.

    Args:
        clip: The audio to measure.
        frame_ms: Length of each analysis frame, in milliseconds.

    Returns:
        Array of per-frame loudness in dBFS, or an empty array if the clip is shorter than one
        frame.
    """
    # Collapse to mono so multi-channel clips get a single loudness value per frame.
    samples = np.array(clip.get_array_of_samples(), dtype=np.float64)
    if clip.channels > 1:
        samples = samples.reshape(-1, clip.channels).mean(axis=1)

    # Split the signal into fixed-length frames; drop any leftover tail that
    # doesn't fill a full frame.
    frame_len = max(1, int(clip.frame_rate * frame_ms / 1000))
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return np.array([])

    # Compute RMS loudness per frame and convert to dBFS relative to the format's max amplitude.
    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    # Floor RMS at 1.0 to avoid log(0) on true digital silence.
    rms = np.clip(rms, 1.0, None)
    max_amp = float(2 ** (clip.sample_width * 8 - 1))
    return 20 * np.log10(rms / max_amp)


def estimate_snr_db(clip: AudioSegment) -> float | None:
    """Estimate signal-to-noise ratio across the whole recording, independent of silence detection:
    compares the loudest frames (song peaks) against the quietest frames (noise floor), by
    percentile rather than a fixed threshold. This still works on recordings with an elevated
    background noise level throughout, where nothing ever drops low enough to be classified as
    "silence" -- detect_nonsilent-based measurement would wrongly read those as having no ambience
    to judge, letting the noisiest recordings slip through unmeasured.

    Args:
        clip: The audio to measure.

    Returns:
        Estimated SNR in dB, or None if the clip is too short to measure reliably (fewer than
        MIN_SNR_FRAMES frames).
    """
    frame_levels = _frame_dbfs(clip)
    if len(frame_levels) < MIN_SNR_FRAMES:
        return None
    # Noise floor and signal level are read off the loudness distribution by percentile,
    # not a fixed threshold.
    noise_floor = float(np.percentile(frame_levels, SNR_NOISE_PERCENTILE))
    signal_level = float(np.percentile(frame_levels, SNR_SIGNAL_PERCENTILE))
    return signal_level - noise_floor


def _generate_noise(
    duration_ms: int,
    reference: AudioSegment,
    noise_type: str = "brown",
    gain_db: float = NOISE_GAIN_DB,
) -> AudioSegment:
    """Fallback ambient bed for recordings without usable dead air.

    Args:
        duration_ms: Length of noise to generate, in milliseconds.
        reference: Clip to match format (sample rate, width, channels) against.
        noise_type: "brown" for low-frequency-weighted noise, anything else for flat white noise.
        gain_db: Output gain applied to the generated noise, in dB.

    Returns:
        A synthetic ambient-noise AudioSegment of the requested duration.
    """
    if duration_ms <= 0:
        return AudioSegment.silent(duration=0)

    # Generate white noise matching the reference clip's sample rate and channel count.
    n_samples = int(reference.frame_rate * duration_ms / 1000)
    rng = np.random.default_rng()
    white = rng.normal(0, 1, size=(n_samples, reference.channels))

    # Brown noise is white noise integrated over time, which shifts its energy
    # toward low frequencies.
    if noise_type == "brown":
        raw = np.cumsum(white, axis=0)
        raw -= raw.mean(axis=0)
    else:
        raw = white

    # Normalize to full scale before applying the target gain.
    peak = np.max(np.abs(raw))
    if peak > 0:
        raw = raw / peak

    amplitude = 10 ** (gain_db / 20)
    raw = raw * amplitude

    # Quantize to the reference clip's PCM format.
    dtype = _NP_DTYPE[reference.sample_width]
    max_int = 2 ** (reference.sample_width * 8 - 1) - 1
    pcm = np.clip(raw * max_int, -max_int - 1, max_int).astype(dtype)

    return AudioSegment(
        pcm.tobytes(),
        frame_rate=reference.frame_rate,
        sample_width=reference.sample_width,
        channels=reference.channels,
    )


def _safe_crossfade(a: AudioSegment, b: AudioSegment, crossfade_ms: int = CROSSFADE_MS) -> AudioSegment:
    """Join two clips with a crossfade, clamped to what both clips can support.

    Args:
        a: The leading clip.
        b: The trailing clip.
        crossfade_ms: Desired crossfade length, in milliseconds.

    Returns:
        The concatenated clip. Falls back to a hard join (no crossfade) if either clip is too short
        to support one.
    """
    fade = min(crossfade_ms, len(a), len(b))
    if fade <= 0:
        return a + b
    return a.append(b, crossfade=fade)


def _tile_to_length(bed: AudioSegment, target_ms: int) -> AudioSegment:
    """Repeat (crossfaded) or trim a clip to an exact target length.

    Args:
        bed: The clip to tile.
        target_ms: Desired output length, in milliseconds.

    Returns:
        A clip of exactly target_ms length. Silence if bed is empty or target_ms is non-positive.
    """
    if target_ms <= 0:
        return AudioSegment.silent(duration=0)
    if len(bed) == 0:
        return AudioSegment.silent(duration=target_ms)

    # Repeat the bed with crossfades until it's long enough, then trim the excess.
    result = bed if len(bed) <= target_ms else bed[:target_ms]
    while len(result) < target_ms:
        result = _safe_crossfade(result, bed)
    return result[:target_ms]


def build_loop(
    song: AudioSegment,
    ambience: AudioSegment,
    target_ms: int = LOOP_TARGET_MS,
    max_ms: int = LOOP_MAX_MS,
    noise_type: str = "brown",
) -> AudioSegment:
    """Repeat the song every GAP_MS.

    Gaps between repeats are filled with real ambience captured from the recording's own dead air,
    tiled to length. If a recording doesn't have enough usable dead air, falls back to synthetic
    ambient noise.

    The gap size is fixed, so spacing between repeats stays consistent -- and songs long enough that
    a full gap-plus-song cycle wouldn't fit twice into target_ms get the clip's total length
    extended beyond target_ms, up to max_ms, so every song gets at least one repeat rather than
    playing once and stopping. If even two repeats wouldn't fit within max_ms, it plays once -- no
    trailing ambience filler gets tacked on to pad it out.

    Args:
        song: The extracted song clip to repeat.
        ambience: Real ambience captured around the song, used to fill gaps.
        target_ms: Nominal target length for the finished loop, in milliseconds.
        max_ms: Hard cap on loop length, in milliseconds.
        noise_type: Noise type to pass to _generate_noise if ambience is too short.

    Returns:
        The finished loop clip.
    """
    # Songs already at or beyond max_ms play once, with no gap filler appended.
    if len(song) >= max_ms:
        return song

    # Extend the target length (up to max_ms) if needed so long songs still get at least one repeat.
    gap_ms = GAP_MS
    two_reps_ms = 2 * len(song) + gap_ms
    effective_total_ms = target_ms if two_reps_ms <= target_ms else min(two_reps_ms, max_ms)

    # Use real ambience if there's enough of it, otherwise synthesize a noise bed.
    bed_source = ambience if len(ambience) >= MIN_AMBIENCE_MS else _generate_noise(
        FALLBACK_NOISE_MS, song, noise_type=noise_type
    )

    # Append (song + gap) pairs until there's no room left for another full cycle.
    result = song
    while True:
        remaining = effective_total_ms - len(result)
        if remaining < gap_ms + len(song):
            break
        gap = _tile_to_length(bed_source, gap_ms)
        result = _safe_crossfade(_safe_crossfade(result, gap), song)
    return result
