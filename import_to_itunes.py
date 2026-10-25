#!/usr/bin/env python3
"""
Import approved files to iTunes for Tanya's DJ workflow.
- Imports FLAC, WAV, MP3, AIFF, AAC directly (no ALAC conversion — XDJ-RR needs WAV/FLAC/MP3)
- Adds files one at a time via temp AppleScript files (handles special chars in filenames)
- Updates the iTunes library cache after import

Usage:
    python3 import_to_itunes.py <folder>    # import all audio from folder
"""

import sys
import subprocess
import tempfile
import os
from pathlib import Path

# Data lives in the music folder (override with DJ_DIR); code lives here.
# Defaults match the original layout, so behaviour is unchanged in place.
BASE = Path(os.environ.get("DJ_DIR", Path.home() / "Music" / "DJ Downloads")).expanduser()
LIBRARY_CACHE = BASE / "itunes_library.json"


def add_to_itunes(file_paths):
    """Add files to iTunes one at a time via temp AppleScript files (handles special chars)."""
    if not file_paths:
        return 0

    added = 0
    failed = []
    for p in file_paths:
        posix = str(p).replace('\\', '\\\\').replace('"', '\\"')
        script = f'tell application "Music"\nadd POSIX file "{posix}"\nend tell'
        with tempfile.NamedTemporaryFile(mode='w', suffix='.applescript', delete=False) as tf:
            tf.write(script)
            tf_path = tf.name
        result = subprocess.run(['osascript', tf_path], capture_output=True, text=True, timeout=60)
        os.unlink(tf_path)
        if result.returncode == 0:
            added += 1
        else:
            failed.append((p.name, result.stderr.strip()))

    if failed:
        print(f"\nFailed to import {len(failed)} files:")
        for name, err in failed:
            print(f"  ✗ {name}: {err}")
    return added


def update_library_cache():
    """Refresh iTunes library JSON cache."""
    script = '''
    tell application "Music"
        set output to ""
        repeat with t in (get every track of library playlist 1)
            set output to output & (name of t) & "|||" & (artist of t) & "~~~"
        end repeat
        return output
    end tell
    '''
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=300)
    raw = result.stdout.strip()
    import json
    from datetime import date
    library = []
    for entry in raw.split('~~~'):
        if '|||' in entry:
            parts = entry.split('|||')
            library.append({'title': parts[0].strip(), 'artist': parts[1].strip() if len(parts) > 1 else ''})
    cache = {'updated': str(date.today()), 'count': len(library), 'tracks': library}
    with open(LIBRARY_CACHE, 'w') as f:
        json.dump(cache, f, indent=2)
    print(f"Library cache updated: {len(library)} tracks")


def main():
    if len(sys.argv) < 2:
        print("Usage: import_to_itunes.py <folder>")
        sys.exit(1)

    folder = Path(sys.argv[1]).expanduser()
    if not folder.exists():
        print(f"Folder not found: {folder}")
        sys.exit(1)

    print(f"\nImporting from: {folder}\n")

    # Collect all audio files directly — no FLAC conversion needed
    to_add = []
    for ext in ['.mp3', '.wav', '.flac', '.aif', '.aiff', '.m4a']:
        to_add.extend(p for p in folder.glob(f'*{ext}') if p.name != '_index.csv')

    print(f"\nAdding {len(to_add)} files to iTunes...", flush=True)
    added = add_to_itunes(to_add)
    print(f"Added: {added} files")

    # Update cache
    print("\nUpdating library cache...")
    update_library_cache()
    print("\nDone.")


if __name__ == '__main__':
    main()
