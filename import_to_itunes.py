#!/usr/bin/env python3
"""
Import approved files to Apple Music (iTunes) for Tanya's DJ workflow.

Three things this handles that the obvious version gets wrong:

1. ⛔ APPLE MUSIC CANNOT PLAY FLAC. `add POSIX file` on a .flac returns exit
   code 0 and adds NOTHING. The old version counted that as success, so every
   FLAC the pipeline ever downloaded was silently dropped — and `.sldl.conf`
   has `pref-format = wav,flac,mp3`, so that was a lot of them. FLACs are now
   transcoded to WAV first (lossless, bit-depth preserved), then imported.

2. EVERY ADD IS VERIFIED. The AppleScript returns the new track's database ID.
   No ID means it did not land, whatever the exit code said.

3. ITUNES IGNORES WAV TAGS. A WAV's LIST/INFO chunk is not read on import, so
   WAVs arrive with no artist, album, or comment — which is why only 73 of her
   166 WAVs carried a camelot comment. Tags are now read from the source file
   before import and written back over AppleScript afterwards.

Usage:
    python3 import_to_itunes.py <folder>
    python3 import_to_itunes.py <folder> --keep-flac   # skip FLACs, don't convert
    python3 import_to_itunes.py <folder> --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

# Data lives in the music folder (override with DJ_DIR); code lives here.
BASE = Path(os.environ.get("DJ_DIR", Path.home() / "Music" / "DJ Downloads")).expanduser()
LIBRARY_CACHE = BASE / "itunes_library.json"

AUDIO_EXTS = ['.mp3', '.wav', '.flac', '.aif', '.aiff', '.m4a']

# Apple Music will not import these at all. Transcode or skip.
UNSUPPORTED = {'.flac'}

# Formats where Apple Music ignores the file's tags on import, so they have to
# be written over AppleScript after the add.
TAG_BLIND = {'.wav', '.aif', '.aiff'}

# ffprobe tag key -> Music property name
TAG_MAP = [
    ("title",        "name"),
    ("artist",       "artist"),
    ("album",        "album"),
    ("album_artist", "album artist"),
    ("composer",     "composer"),
    ("genre",        "genre"),
    ("comment",      "comment"),
]


def esc(s):
    """Escape a value for embedding in an AppleScript string literal."""
    return str(s).replace('\\', '\\\\').replace('"', '\\"')


def run_osa(script, timeout=120):
    """Run AppleScript from a temp file so long scripts and odd chars survive."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.applescript',
                                     delete=False, encoding='utf-8') as tf:
        tf.write(script)
        tf_path = tf.name
    try:
        return subprocess.run(['osascript', tf_path], capture_output=True,
                              text=True, timeout=timeout)
    finally:
        os.unlink(tf_path)


def read_tags(path):
    """Read tags + bit depth via ffprobe. Uniform across flac/mp3/wav/aiff/m4a."""
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', str(path)],
            capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout or '{}')
    except Exception:
        return {}, None

    tags = {k.lower(): v for k, v in (data.get('format', {}).get('tags') or {}).items()}
    depth = None
    for st in data.get('streams', []):
        if st.get('codec_type') == 'audio':
            depth = st.get('bits_per_raw_sample') or st.get('bits_per_sample')
            break
    try:
        depth = int(depth) if depth else None
    except (TypeError, ValueError):
        depth = None
    return tags, depth


def tags_from_filename(path):
    """Fallback when a file carries no tags: 'Artist - Title.wav'."""
    stem = path.stem
    if ' - ' in stem:
        artist, title = stem.split(' - ', 1)
        return {'artist': artist.strip(), 'title': title.strip()}
    return {'title': stem.strip()}


def transcode_to_wav(src):
    """
    FLAC -> WAV, lossless, preserving sample rate and bit depth.
    Returns the new path, or None on failure.
    """
    tags, depth = read_tags(src)
    codec = {16: 'pcm_s16le', 24: 'pcm_s24le', 32: 'pcm_s32le'}.get(depth or 16, 'pcm_s24le')
    dst = src.with_suffix('.wav')
    if dst.exists():
        dst = src.with_name(src.stem + '_converted.wav')

    cmd = ['ffmpeg', '-hide_banner', '-v', 'error', '-y', '-i', str(src),
           '-map', '0:a:0', '-c:a', codec]
    # Carry tags into the WAV INFO chunk. Music ignores them, but rekordbox and
    # any other tool reading the file directly will not.
    for key in ('title', 'artist', 'album', 'genre', 'comment', 'date', 'composer'):
        if tags.get(key):
            cmd += ['-metadata', f'{key}={tags[key]}']
    cmd.append(str(dst))

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not dst.exists():
        print(f"     ✗ transcode failed: {r.stderr.strip()[:160]}")
        return None
    print(f"     → {dst.name}  ({codec.replace('pcm_s', '').replace('le', '')}-bit WAV)")
    return dst


def add_one(path):
    """
    Add a single file and return its database ID, or None.
    The ID is the only honest proof it landed.
    """
    script = (
        'tell application "Music"\n'
        f'  set t to add POSIX file "{esc(path)}"\n'
        '  return (database ID of t) as string\n'
        'end tell'
    )
    r = run_osa(script, timeout=120)
    out = (r.stdout or '').strip()
    if r.returncode == 0 and out.isdigit():
        return int(out)
    return None


def apply_tags(dbid, tags, attempts=5):
    """
    Write tags onto an already-imported track. Returns list of fields set.

    ⚠️ RETRIES ON PURPOSE. `add POSIX file` returns as soon as Music accepts
    the file, but Music is still copying it into the media folder — and a
    `set name of t` against a track mid-copy fails with "Parameter error
    (-50)". A 72MB WAV reliably lost every tag this way. The add is fine; only
    the write has to wait. Found 2026-11-01.
    """
    lines, applied = [], []
    for key, prop in TAG_MAP:
        val = tags.get(key)
        if val:
            lines.append(f'  set {prop} of t to "{esc(val)}"')
            applied.append(prop)

    # Year and track number are numeric properties, not strings.
    raw_year = (tags.get('date') or tags.get('year') or '')[:4]
    if raw_year.isdigit():
        lines.append(f'  set year of t to {int(raw_year)}')
        applied.append('year')
    for key, prop in (('track', 'track number'), ('disc', 'disc number')):
        raw = str(tags.get(key, '')).split('/')[0]
        if raw.isdigit():
            lines.append(f'  set {prop} of t to {int(raw)}')
            applied.append(prop)

    if not lines:
        return []

    script = ('tell application "Music"\n'
              f'  set t to (first track of library playlist 1 whose database ID is {dbid})\n'
              + '\n'.join(lines) + '\n'
              'end tell')

    last = ""
    for i in range(attempts):
        r = run_osa(script)
        if r.returncode == 0:
            return applied
        last = (r.stderr or '').strip()
        # -50 means Music is still ingesting the file. Back off and retry.
        if '-50' not in last and 'Parameter error' not in last:
            break
        time.sleep(1.5 * (i + 1))

    print(f"     ⚠ tag write failed after {attempts} attempt(s): {last[:150]}")
    return []


def update_library_cache():
    script = '''
    tell application "Music"
        set output to ""
        repeat with t in (get every track of library playlist 1)
            set output to output & (name of t) & "|||" & (artist of t) & "~~~"
        end repeat
        return output
    end tell
    '''
    result = subprocess.run(['osascript', '-e', script],
                            capture_output=True, text=True, timeout=300)
    library = []
    for entry in (result.stdout or '').strip().split('~~~'):
        if '|||' in entry:
            parts = entry.split('|||')
            library.append({'title': parts[0].strip(),
                            'artist': parts[1].strip() if len(parts) > 1 else ''})
    cache = {'updated': str(date.today()), 'count': len(library), 'tracks': library}
    LIBRARY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(LIBRARY_CACHE, 'w') as f:
        json.dump(cache, f, indent=2)
    print(f"Library cache updated: {len(library)} tracks")
    return len(library)


def main():
    ap = argparse.ArgumentParser(description="Import audio to Apple Music, verified.")
    ap.add_argument("folder", help="folder of audio files to import")
    ap.add_argument("--keep-flac", action="store_true",
                    help="skip FLACs instead of transcoding them to WAV")
    ap.add_argument("--no-cache", action="store_true", help="skip the library cache refresh")
    ap.add_argument("--dry-run", action="store_true", help="list what would happen, change nothing")
    args = ap.parse_args()

    folder = Path(args.folder).expanduser()
    if not folder.exists():
        sys.exit(f"Folder not found: {folder}")

    files = sorted(p for ext in AUDIO_EXTS for p in folder.glob(f'*{ext}'))
    if not files:
        print(f"No audio files found in {folder}")
        return 0

    print(f"\nImporting from: {folder}")
    print(f"Found {len(files)} audio file(s)\n")

    imported, skipped, failed = [], [], []

    for src in files:
        print(f"  {src.name}")
        path = src

        if src.suffix.lower() in UNSUPPORTED:
            if args.keep_flac:
                print("     ⊘ skipped — Apple Music cannot play FLAC (--keep-flac)")
                skipped.append(src.name)
                continue
            print("     FLAC — Apple Music cannot play it; transcoding to WAV")
            if args.dry_run:
                print("     (dry run)")
                continue
            path = transcode_to_wav(src)
            if path is None:
                failed.append((src.name, "transcode failed"))
                continue

        if args.dry_run:
            print("     (dry run — would import)")
            continue

        # Read tags from the ORIGINAL file, not the transcoded one. A WAV INFO
        # chunk cannot hold everything a FLAC can — album_artist and composer
        # are silently dropped in transcode — so `src` is the richer source.
        tags, _ = read_tags(src)
        if path != src:
            # Anything the WAV does carry wins, in case ffmpeg normalised it.
            wav_tags, _ = read_tags(path)
            tags = {**tags, **{k: v for k, v in wav_tags.items() if v}}
        tags.pop('encoder', None)
        if not tags.get('title') and not tags.get('artist'):
            tags = {**tags_from_filename(path), **tags}

        dbid = add_one(path)
        if dbid is None:
            print("     ✗ NOT ADDED — Music accepted the command but no track appeared")
            failed.append((path.name, "add returned no database ID"))
            continue

        note = ""
        if path.suffix.lower() in TAG_BLIND:
            applied = apply_tags(dbid, tags)
            note = f" | tags set: {', '.join(applied)}" if applied else " | ⚠ no tags to set"
        print(f"     ✓ imported (id {dbid}){note}")
        imported.append((path.name, dbid))

    print("\n" + "=" * 58)
    print(f"  imported {len(imported)} | skipped {len(skipped)} | failed {len(failed)}")
    print("=" * 58)
    if failed:
        print("\nFAILED:")
        for name, why in failed:
            print(f"  ✗ {name} — {why}")

    if imported and not args.no_cache and not args.dry_run:
        print("\nUpdating library cache...")
        update_library_cache()

    print()
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
