#!/usr/bin/env python3
"""
Spotify URL resolver for Tanya's DJ workflow.
Resolves Spotify track URLs to artist + title using the embed API (no credentials needed).
Cross-references against iTunes library cache to remove dupes.

Usage:
    python3 resolve_spotify.py urls.txt          # resolve from file (one URL per line)
    python3 resolve_spotify.py [url] [url] ...   # resolve from args
    python3 resolve_spotify.py --update-library  # refresh iTunes library cache only

Output: CSV written to ~/Music/DJ Downloads/resolved_YYYY-MM-DD.csv
"""

import sys
import json
import re
import time
import urllib.request
import subprocess
from pathlib import Path
from datetime import date

BASE = Path(__file__).parent
LIBRARY_CACHE = BASE / "itunes_library.json"
OUTPUT_DIR = BASE


def get_track_info(track_url):
    track_id = track_url.strip().split('/')[-1].split('?')[0]
    embed_url = f"https://open.spotify.com/embed/track/{track_id}"
    req = urllib.request.Request(embed_url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'text/html',
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode('utf-8', errors='replace')
    next_data = re.findall(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
    if next_data:
        data = json.loads(next_data[0])
        entity = data['props']['pageProps']['state']['data']['entity']
        title = entity['name']
        artists = [a['name'] for a in entity.get('artists', [])]
        return title, artists
    return None, []


def update_library_cache():
    """Dump iTunes library to JSON cache via AppleScript."""
    print("Refreshing iTunes library cache...", flush=True)
    script = '''
    tell application "Music"
        set output to ""
        repeat with t in (get every track of library playlist 1)
            set output to output & (name of t) & "|||" & (artist of t) & "~~~"
        end repeat
        return output
    end tell
    '''
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=60)
    raw = result.stdout.strip()

    library = []
    for entry in raw.split('~~~'):
        if '|||' in entry:
            parts = entry.split('|||')
            library.append({'title': parts[0].strip(), 'artist': parts[1].strip() if len(parts) > 1 else ''})

    cache = {'updated': str(date.today()), 'count': len(library), 'tracks': library}
    with open(LIBRARY_CACHE, 'w') as f:
        json.dump(cache, f, indent=2)
    print(f"Library cache updated: {len(library)} tracks → {LIBRARY_CACHE}")
    return library


def load_library_cache():
    if not LIBRARY_CACHE.exists():
        return update_library_cache()
    with open(LIBRARY_CACHE) as f:
        cache = json.load(f)
    # Support both legacy list format and new dict format
    if isinstance(cache, list):
        tracks = [{'title': t.get('name', ''), 'artist': t.get('artist', '')} for t in cache]
        print(f"Library cache: {len(tracks)} tracks (legacy format)")
        return tracks
    print(f"Library cache: {cache['count']} tracks (last updated {cache['updated']})")
    return cache['tracks']


def is_dupe(title, library_tracks):
    title_norm = title.lower().strip()
    base = re.split(r'\s[-–]\s|\s\(', title_norm)[0].strip()
    lib_titles = {t['title'].lower().strip() for t in library_tracks}
    lib_bases = {re.split(r'\s[-–]\s|\s\(', t['title'].lower().strip())[0].strip() for t in library_tracks}
    return title_norm in lib_titles or base in lib_bases


def main():
    args = sys.argv[1:]

    if '--update-library' in args:
        update_library_cache()
        return

    # Collect URLs
    urls = []
    for arg in args:
        if arg.startswith('http'):
            urls.append(arg)
        elif Path(arg).exists():
            with open(arg) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('http'):
                        urls.append(line)

    if not urls:
        print("No URLs provided. Usage: resolve_spotify.py [urls.txt | url url ...]")
        sys.exit(1)

    library = load_library_cache()
    print(f"\nResolving {len(urls)} URLs...\n")

    results = []
    dupes = []
    failed = []

    for i, url in enumerate(urls):
        try:
            title, artists = get_track_info(url)
            artist_str = ', '.join(artists)
            if is_dupe(title, library):
                dupes.append((i+1, artist_str, title))
                print(f"  {i+1:3}. [DUPE] {artist_str} — {title}", flush=True)
            else:
                results.append((i+1, artist_str, title))
                print(f"  {i+1:3}. {artist_str} — {title}", flush=True)
        except Exception as e:
            failed.append((i+1, url, str(e)))
            print(f"  {i+1:3}. [FAILED] {url.split('/')[-1]}", flush=True)
        time.sleep(0.15)

    # Write output CSV
    out_file = OUTPUT_DIR / f"resolved_{date.today()}.csv"
    with open(out_file, 'w') as f:
        f.write("Artist,Title\n")
        for _, artist, title in results:
            # Escape commas
            a = artist.replace('"', '""')
            t = title.replace('"', '""')
            f.write(f'"{a}","{t}"\n')

    print(f"\n{'='*50}")
    print(f"Resolved: {len(results)} to download | {len(dupes)} dupes skipped | {len(failed)} failed")
    print(f"Output CSV: {out_file}")
    if dupes:
        print(f"\nDupes ({len(dupes)}):")
        for _, a, t in dupes:
            print(f"  {a} — {t}")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for _, url, err in failed:
            print(f"  {url}")


if __name__ == '__main__':
    main()
