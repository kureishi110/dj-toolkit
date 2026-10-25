#!/usr/bin/env python3
"""
recover_batches.py — Rescue unimported tracks from all historical download batches.

Scans every audio file in DJ Downloads (excluding what's already in the iTunes
media folder), quality-checks it, skips dupes already in iTunes, analyzes
key+energy, converts FLACs to ALAC, and imports to iTunes.

Usage:
    python3 ~/Music/DJ\ Downloads/recover_batches.py
    python3 ~/Music/DJ\ Downloads/recover_batches.py --dry-run   # preview only
"""

import sys
import os
import re
import json
import subprocess
from pathlib import Path
from datetime import date
from typing import Optional

DJ_DIR = Path.home() / "Music" / "DJ Downloads"
sys.path.insert(0, str(DJ_DIR))

from check_quality import analyze_file  # reuse quality checker

AUDIO_EXTS   = {'.mp3', '.wav', '.flac', '.aiff', '.aif'}
SKIP_SUBDIRS = {'converted', 'spectrograms'}

DRY_RUN = '--dry-run' in sys.argv


# ── File discovery ─────────────────────────────────────────────────────────────

def find_all_audio_files(root: Path) -> list[Path]:
    files = []
    for f in root.rglob('*'):
        if not (f.is_file() and f.suffix.lower() in AUDIO_EXTS):
            continue
        # Skip the actual iTunes media folder
        if '/Music/Music/' in str(f):
            continue
        # Skip converted/ and spectrograms/ subdirs
        if any(part in SKIP_SUBDIRS for part in f.parts):
            continue
        files.append(f)
    return sorted(files)


# ── Metadata helpers ──────────────────────────────────────────────────────────

def get_title_from_metadata(filepath: Path) -> str:
    """Read title tag; fall back to a cleaned filename stem."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(filepath, easy=True)
        if audio and 'title' in audio and audio['title']:
            return audio['title'][0].strip()
    except Exception:
        pass
    stem = filepath.stem
    stem = re.sub(r'^[\d]+[-_.\s]+', '', stem)      # strip leading track numbers
    stem = re.sub(r'[-_]', ' ', stem)               # normalise separators
    return stem.strip()


# ── iTunes dupe check ─────────────────────────────────────────────────────────

def itunes_has_track(title: str) -> bool:
    safe = title.replace('"', '').replace("'", "\\'")
    script = f'''
tell application "Music"
    set matches to (every track whose name contains "{safe}")
    return (count of matches) > 0
end tell'''
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return r.stdout.strip() == 'true'


# ── FLAC conversion ───────────────────────────────────────────────────────────

def convert_flac_to_alac(flac_path: Path) -> Optional[Path]:
    out_dir = flac_path.parent / 'converted'
    out_dir.mkdir(exist_ok=True)
    out = out_dir / (flac_path.stem + '.m4a')
    result = subprocess.run(
        ['ffmpeg', '-y', '-i', str(flac_path), '-c:a', 'alac', '-vn',
         str(out), '-loglevel', 'error'],
        capture_output=True, timeout=120
    )
    return out if out.exists() else None


# ── iTunes import ─────────────────────────────────────────────────────────────

def add_to_itunes(file_path: Path) -> bool:
    script = f'tell application "Music"\nadd POSIX file "{str(file_path)}"\nreturn "done"\nend tell'
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def update_library_cache():
    script = '''
tell application "Music"
    set output to ""
    repeat with t in (get every track of library playlist 1)
        set output to output & (name of t) & "|||" & (artist of t) & "~~~"
    end repeat
    return output
end tell'''
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=300)
    library = []
    for entry in r.stdout.strip().split('~~~'):
        if '|||' in entry:
            parts = entry.split('|||')
            library.append({'title': parts[0].strip(), 'artist': parts[1].strip() if len(parts) > 1 else ''})
    cache_path = DJ_DIR / 'itunes_library.json'
    with open(cache_path, 'w') as f:
        json.dump({'updated': str(date.today()), 'count': len(library), 'tracks': library}, f, indent=2)
    print(f'  Library cache updated: {len(library)} tracks')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print('  recover_batches — DJ Download Recovery')
    if DRY_RUN:
        print('  [DRY RUN — nothing will be imported]')
    print(f"{'='*60}\n")

    # 1. Discover files
    print('Step 1: Scanning for audio files...')
    all_files = find_all_audio_files(DJ_DIR)
    print(f'  Found {len(all_files)} audio files across all batch folders\n')

    # 2. Quality check
    print('Step 2: Quality checking (PASS only — WARNs/FLAGs/REJECTs skipped)...')
    pass_files  = []
    skipped_qc  = []

    for f in all_files:
        result = analyze_file(f)
        status = result['status']
        if status == 'PASS':
            pass_files.append(f)
        else:
            reason = result['issues'][0] if result['issues'] else 'quality check failed'
            skipped_qc.append((f, status, reason))
            print(f'  [{status}] {f.name}')
            if result['issues']:
                print(f'          → {reason}')

    print(f'\n  {len(pass_files)} pass | {len(skipped_qc)} skipped\n')

    if not pass_files:
        print('  No files pass quality check. Done.')
        return

    # 3. iTunes dupe check
    print('Step 3: Checking iTunes for duplicates...')
    to_import    = []
    already_owned = []

    for f in pass_files:
        title = get_title_from_metadata(f)
        if itunes_has_track(title):
            already_owned.append((f, title))
            print(f'  ✓ already in iTunes: {title}')
        else:
            to_import.append(f)

    print(f'\n  {len(already_owned)} already in iTunes | {len(to_import)} need importing\n')

    if not to_import:
        print('  All passing tracks already in iTunes. Done.')
        return

    print('  Tracks to import:')
    for f in to_import:
        print(f'    · {f.name}')

    if DRY_RUN:
        print('\n[DRY RUN] Would have imported the above. Re-run without --dry-run to proceed.')
        return

    # 4. Key + energy analysis
    print('\nStep 4: Key + energy analysis...')
    list_file = Path('/tmp/recover_batch_files.txt')
    list_file.write_text('\n'.join(str(f) for f in to_import))
    subprocess.run(
        ['python3', str(DJ_DIR / 'analyze_tracks.py'), '--from-file', str(list_file)],
        timeout=1800
    )

    # 5. Import
    print(f'\nStep 5: Importing {len(to_import)} tracks to iTunes...')
    imported = 0
    failed   = []

    for f in to_import:
        print(f'  {f.name}', flush=True)
        if f.suffix.lower() == '.flac':
            alac = convert_flac_to_alac(f)
            if not alac:
                print('    ✗ FLAC→ALAC conversion failed')
                failed.append(f)
                continue
            ok = add_to_itunes(alac)
        else:
            ok = add_to_itunes(f)

        if ok:
            imported += 1
            print('    ✓')
        else:
            print('    ✗ iTunes import failed')
            failed.append(f)

    # 6. Update cache
    print('\nStep 6: Updating library cache...')
    update_library_cache()

    # Summary
    print(f"\n{'='*60}")
    print('  RECOVERY COMPLETE')
    print(f"{'='*60}")
    print(f'  Scanned:           {len(all_files)} files')
    print(f'  Passed QC:         {len(pass_files)}')
    print(f'  Already in iTunes: {len(already_owned)}')
    print(f'  Imported:          {imported}')
    if failed:
        print(f'  Failed:            {len(failed)}')
        for f in failed:
            print(f'    - {f.name}')
    if skipped_qc:
        print(f'\n  Skipped (quality):')
        for f, status, _reason in skipped_qc:
            print(f'    [{status}] {f.name}')
    print()


if __name__ == '__main__':
    main()
