#!/usr/bin/env python3
"""
DJ Quality Checker
Analyzes audio files in _incomplete/, generates spectrogram images,
detects frequency cutoffs, and flags suspicious files.
"""

import os
import sys
import subprocess
import numpy as np
from pathlib import Path

try:
    import librosa
    import librosa.display
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip3 install librosa numpy matplotlib")
    sys.exit(1)

# Data lives in the music folder (override with DJ_DIR); code lives here.
# Defaults match the original layout, so behaviour is unchanged in place.
BASE = Path(os.environ.get("DJ_DIR", Path.home() / "Music" / "DJ Downloads")).expanduser()
INCOMPLETE_DIR = BASE / "_incomplete"
SPECTROGRAM_DIR = BASE / "_review" / "spectrograms"
SPECTROGRAM_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_EXTENSIONS = {'.wav', '.flac', '.mp3', '.aiff', '.aif', '.m4a'}

# Frequency cutoff thresholds (Hz) that indicate upconverted lossy files
# Genuine WAV/FLAC: content up to 20kHz+
# 320kbps MP3: cutoff ~20kHz (acceptable)
# 128kbps MP3 upconverted: cutoff ~16kHz (bad)
# 64kbps upconverted: cutoff ~14kHz or lower (very bad)
CUTOFF_GOOD = 18000      # 18kHz+ = genuine high quality
CUTOFF_WARN = 16000      # 16-18kHz = borderline, likely 256kbps source
CUTOFF_BAD  = 14000      # below 16kHz = upconverted low quality, reject


def get_frequency_cutoff(filepath):
    """
    Returns the highest frequency with meaningful energy content.
    Uses librosa to compute a mel spectrogram and find where energy drops off.
    """
    try:
        y, sr = librosa.load(str(filepath), sr=None, mono=True, duration=60)
        # Use a short segment from 30s in (past intros) if track is long enough
        if len(y) > sr * 60:
            y = y[sr * 30: sr * 90]

        # Compute STFT
        D = np.abs(librosa.stft(y, n_fft=4096))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)

        # Average power across time for each frequency bin
        mean_power = D.mean(axis=1)

        # Normalize
        mean_power_db = librosa.amplitude_to_db(mean_power, ref=np.max)

        # Find highest frequency bin with power above -60dB (meaningful energy)
        threshold_db = -60
        meaningful = np.where(mean_power_db > threshold_db)[0]
        if len(meaningful) == 0:
            return 0
        return int(freqs[meaningful[-1]])

    except Exception as e:
        return None, str(e)


def generate_spectrogram(filepath, output_path):
    """Generates a spectrogram image using ffmpeg."""
    try:
        cmd = [
            'ffmpeg', '-y', '-i', str(filepath),
            '-lavfi', 'showspectrumpic=s=1280x512:mode=combined:color=intensity:scale=log',
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, timeout=60)
        return output_path.exists()
    except Exception:
        return False


def check_mp3_bitrate(filepath):
    """Returns bitrate for MP3 files using ffprobe."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_streams', str(filepath)],
            capture_output=True, text=True, timeout=15
        )
        import json
        data = json.loads(result.stdout)
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'audio':
                bitrate = int(stream.get('bit_rate', 0)) // 1000
                return bitrate
    except Exception:
        pass
    return None


def analyze_file(filepath):
    """Full analysis of a single file. Returns a result dict."""
    result = {
        'file': filepath.name,
        'path': filepath,
        'format': filepath.suffix.lower(),
        'status': 'PASS',
        'issues': [],
        'cutoff_hz': None,
        'bitrate_kbps': None,
        'spectrogram': None,
    }

    # Check MP3 bitrate
    if filepath.suffix.lower() == '.mp3':
        bitrate = check_mp3_bitrate(filepath)
        result['bitrate_kbps'] = bitrate
        if bitrate and bitrate < 320:
            result['issues'].append(f"MP3 bitrate {bitrate}kbps (minimum 320kbps required)")
            result['status'] = 'FLAG'

    # Generate spectrogram image
    spec_path = SPECTROGRAM_DIR / (filepath.stem + '_spectrogram.png')
    generated = generate_spectrogram(filepath, spec_path)
    if generated:
        result['spectrogram'] = spec_path

    # Frequency cutoff analysis
    cutoff = get_frequency_cutoff(filepath)
    if isinstance(cutoff, tuple):
        result['issues'].append(f"Could not analyze: {cutoff[1]}")
        result['status'] = 'FLAG'
    elif cutoff is not None:
        result['cutoff_hz'] = cutoff
        if filepath.suffix.lower() in {'.wav', '.flac', '.aiff', '.aif'}:
            # For lossless formats, a low cutoff means it's a fake/upconverted file
            if cutoff < CUTOFF_BAD:
                result['issues'].append(
                    f"Frequency cutoff at {cutoff//1000}kHz — looks like upconverted low-quality source. Reject."
                )
                result['status'] = 'REJECT'
            elif cutoff < CUTOFF_WARN:
                result['issues'].append(
                    f"Frequency cutoff at {cutoff//1000}kHz — possible upconverted 128-256kbps source. Check spectrogram."
                )
                result['status'] = 'FLAG'
            elif cutoff < CUTOFF_GOOD:
                result['issues'].append(
                    f"Frequency cutoff at {cutoff//1000}kHz — borderline. Probably 256kbps source."
                )
                result['status'] = 'WARN'

    return result


def main(folder=None, quiet=True):
    """
    Run quality check on a folder of audio files.
    Args:
        folder: Path to scan. Defaults to INCOMPLETE_DIR.
        quiet:  If True (default), suppress PASS output — only show problems.
    """
    target = Path(folder) if folder else INCOMPLETE_DIR
    files = [
        f for f in target.rglob('*')
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        and '.incomplete' not in f.suffixes
    ]

    if not files:
        print(f"No audio files found in {target}")
        return []

    print(f"\nAnalyzing {len(files)} file(s)...\n")

    results = []
    for f in sorted(files):
        result = analyze_file(f)
        results.append(result)
        # In quiet mode only print non-PASS inline
        if not quiet or result['status'] != 'PASS':
            cutoff = f" | {result['cutoff_hz']//1000}kHz" if result['cutoff_hz'] else ""
            bitrate = f" | {result['bitrate_kbps']}kbps" if result['bitrate_kbps'] else ""
            print(f"  [{result['status']}] {f.name}{bitrate}{cutoff}", flush=True)

    passed   = [r for r in results if r['status'] == 'PASS']
    warned   = [r for r in results if r['status'] == 'WARN']
    flagged  = [r for r in results if r['status'] == 'FLAG']
    rejected = [r for r in results if r['status'] == 'REJECT']

    print("\n" + "="*60)
    print("QUALITY REPORT")
    print("="*60)

    if not quiet and passed:
        print(f"\n✅ PASS ({len(passed)})")
        for r in passed:
            cutoff = f" — {r['cutoff_hz']//1000}kHz" if r['cutoff_hz'] else ""
            bitrate = f" {r['bitrate_kbps']}kbps" if r['bitrate_kbps'] else ""
            print(f"   {r['file']}{bitrate}{cutoff}")
    elif quiet and passed:
        print(f"\n✅ PASS: {len(passed)} files — all good, not listed")

    if warned:
        print(f"\n⚠️  WARN ({len(warned)}) — upconverted lossless, borderline quality")
        for r in warned:
            print(f"   {r['file']}")
            for issue in r['issues']:
                print(f"     → {issue}")

    if flagged:
        print(f"\n🚩 FLAG ({len(flagged)}) — needs review")
        for r in flagged:
            print(f"   {r['file']}")
            for issue in r['issues']:
                print(f"     → {issue}")
            if r['spectrogram']:
                print(f"     spectrogram: {r['spectrogram']}")

    if rejected:
        print(f"\n❌ REJECT ({len(rejected)}) — do not use")
        for r in rejected:
            print(f"   {r['file']}")
            for issue in r['issues']:
                print(f"     → {issue}")

    print(f"\nSummary: {len(passed)} pass | {len(warned)} warn | {len(flagged)} flag | {len(rejected)} reject")
    print(f"Spectrograms: {SPECTROGRAM_DIR}\n")
    return results


if __name__ == '__main__':
    main()
