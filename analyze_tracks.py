#!/usr/bin/env python3
"""
analyze_tracks.py — Key (Camelot) + Energy (1-10) analysis for DJ library.
Writes results to the comment tag so they appear in Rekordbox.

Usage:
    python3 analyze_tracks.py <file_or_folder> [<file_or_folder> ...]
    python3 analyze_tracks.py ~/Music/DJ\ Downloads/_incomplete/batch_0505/

Output format in comment tag: "8A Energy 7"
"""

import sys
import os
import glob
import math
import essentia.standard as es
from mutagen.id3 import ID3, COMM, error as ID3Error
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

# Camelot Wheel mapping from essentia key output
# essentia returns (key, scale) e.g. ("C", "major") or ("A", "minor")
CAMELOT = {
    ("C",  "major"): "8B",  ("A",  "minor"): "8A",
    ("G",  "major"): "9B",  ("E",  "minor"): "9A",
    ("D",  "major"): "10B", ("B",  "minor"): "10A",
    ("A",  "major"): "11B", ("F#", "minor"): "11A",
    ("E",  "major"): "12B", ("C#", "minor"): "12A",
    ("B",  "major"): "1B",  ("G#", "minor"): "1A",
    ("F#", "major"): "2B",  ("D#", "minor"): "2A",
    ("Db", "major"): "3B",  ("Bb", "minor"): "3A",
    ("Ab", "major"): "4B",  ("F",  "minor"): "4A",
    ("Eb", "major"): "5B",  ("C",  "minor"): "5A",
    ("Bb", "major"): "6B",  ("G",  "minor"): "6A",
    ("F",  "major"): "7B",  ("D",  "minor"): "7A",
    # Enharmonic equivalents essentia may return
    ("Gb", "major"): "2B",  ("Eb", "minor"): "2A",
    ("Cb", "major"): "3B",  ("Ab", "minor"): "1A",
    ("Gb", "minor"): "11A", ("A#", "minor"): "3A",
    ("A#", "major"): "6B",  ("G#", "major"): "4B",
    ("D#", "major"): "5B",
    # Additional enharmonics
    ("C#", "major"): "3B",  ("C#", "minor"): "12A",
    ("Db", "minor"): "12A", ("G#", "minor"): "1A",
    ("D#", "minor"): "2A",  ("Bb", "minor"): "3A",
}

# Energy model: weighted blend of 4 signals, each normalized to 0-1 then mapped to 1-10.
# Weights tuned to approximate Mixed In Key's dancefloor-energy feel:
#   Loudness (EBUR128)   30% — raw punch
#   Danceability         25% — rhythmic coherence (essentia DFA algorithm)
#   Onset density        25% — how busy/driving the track is (events/sec)
#   Bass energy ratio    20% — kick/sub presence relative to full spectrum
WEIGHTS = {"loudness": 0.30, "dance": 0.25, "onsets": 0.25, "bass": 0.20}

# Normalization ranges — empirically calibrated for dance music
RANGES = {
    "loudness": (-20.0, -5.0),    # LUFS
    "dance":    (0.3,   2.5),     # essentia Danceability output
    "onsets":   (1.0,   8.0),     # onsets per second
    "bass":     (0.05,  0.40),    # fraction of spectral energy below 250Hz
}


def _norm(val: float, key: str) -> float:
    lo, hi = RANGES[key]
    return max(0.0, min(1.0, (val - lo) / (hi - lo)))


def analyze(filepath: str) -> tuple[str, int]:
    """Returns (camelot_key, energy_1_to_10)."""
    mono = es.MonoLoader(filename=filepath, sampleRate=44100)()

    # Key
    key, scale, _ = es.KeyExtractor()(mono)
    camelot = CAMELOT.get((key, scale), f"{key} {scale}")

    # Danceability (rhythmic complexity via DFA)
    danceability, _ = es.Danceability()(mono)

    # Onset density
    _, onset_rate = es.OnsetRate()(mono)

    # Bass energy ratio — frame-by-frame spectral decomposition
    w = es.Windowing(type="hann")
    spectrum = es.Spectrum()
    freq_bands = es.FrequencyBands(
        frequencyBands=[20, 250, 2000, 8000, 20000], sampleRate=44100
    )
    bass_sum = total_sum = 0.0
    for frame in es.FrameGenerator(mono, frameSize=2048, hopSize=512):
        bands = freq_bands(spectrum(w(frame)))
        bass_sum += float(bands[0])
        total_sum += float(sum(bands))
    bass_ratio = bass_sum / max(total_sum, 1e-9)

    # Integrated loudness (EBUR128) — needs stereo signal
    stereo, _, _, _, _, _ = es.AudioLoader(filename=filepath)()
    _, _, integrated_lufs, _ = es.LoudnessEBUR128(hopSize=0.1, startAtZero=True)(stereo)

    # Weighted blend → 1-10
    score = (
        WEIGHTS["loudness"] * _norm(float(integrated_lufs), "loudness")
        + WEIGHTS["dance"]   * _norm(float(danceability),   "dance")
        + WEIGHTS["onsets"]  * _norm(float(onset_rate),     "onsets")
        + WEIGHTS["bass"]    * _norm(float(bass_ratio),     "bass")
    )
    energy = max(1, min(10, round(1 + score * 9)))

    return camelot, energy


def has_comment(filepath: str) -> bool:
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".mp3":
            tags = ID3(filepath)
            return any(k.startswith("COMM") for k in tags.keys())
        elif ext == ".flac":
            tags = FLAC(filepath)
            return bool(tags.get("comment"))
        elif ext in (".m4a", ".alac", ".aac"):
            tags = MP4(filepath)
            return "\xa9cmt" in tags and bool(tags["\xa9cmt"])
    except Exception:
        pass
    return False


def write_comment(filepath: str, comment_text: str):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".mp3":
        try:
            tags = ID3(filepath)
        except ID3Error:
            from mutagen.id3 import ID3NoHeaderError
            tags = ID3()
        # Remove existing COMM tags to avoid duplication
        for key in list(tags.keys()):
            if key.startswith("COMM"):
                del tags[key]
        tags.add(COMM(encoding=3, lang="eng", desc="", text=[comment_text]))
        tags.save(filepath)
    elif ext == ".flac":
        tags = FLAC(filepath)
        tags["comment"] = [comment_text]
        tags.save()
    elif ext in (".m4a", ".alac", ".aac"):
        tags = MP4(filepath)
        tags["\xa9cmt"] = [comment_text]
        tags.save()
    else:
        print(f"  SKIP unsupported format: {filepath}")


SKIP_DIRS = {"converted", "_review", "spectrograms"}

def collect_files(paths: list[str]) -> list[str]:
    files = []
    supported = {".mp3", ".flac", ".m4a", ".aac"}
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            for root, dirs, fnames in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in fnames:
                    if os.path.splitext(f)[1].lower() in supported:
                        files.append(os.path.join(root, f))
        elif os.path.isfile(p):
            files.append(p)
    return sorted(files)


def main():
    args = sys.argv[1:]
    force = "--force" in args
    if force:
        args = [a for a in args if a != "--force"]

    from_file = None
    if "--from-file" in args:
        idx = args.index("--from-file")
        from_file = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if from_file:
        with open(os.path.expanduser(from_file)) as fh:
            extra = [l.strip() for l in fh if l.strip()]
        args = args + extra

    if not args:
        print(__doc__)
        sys.exit(1)

    files = collect_files(args)
    if not files:
        print("No supported audio files found.")
        sys.exit(1)

    print(f"Analyzing {len(files)} tracks (force={force})...\n")
    results = []
    failed = []

    skipped = 0
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        if not force and has_comment(f):
            skipped += 1
            continue
        try:
            camelot, energy = analyze(f)
            comment = f"{camelot} Energy {energy}"
            write_comment(f, comment)
            print(f"  [{i}/{len(files)}] {name}")
            print(f"          → {comment}")
            results.append((name, camelot, energy))
        except Exception as e:
            print(f"  [{i}/{len(files)}] FAIL {name}: {e}")
            failed.append(name)

    print(f"\n{'='*50}")
    print(f"Done: {len(results)} analyzed, {skipped} skipped (already tagged), {len(failed)} failed")
    if failed:
        print("\nFailed:")
        for f in failed:
            print(f"  {f}")


if __name__ == "__main__":
    main()
