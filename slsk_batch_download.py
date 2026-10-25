#!/usr/bin/env python3
"""
slsk_batch_download.py — Autonomous Soulseek batch downloader for DJ tracks.

Searches Soulseek, downloads best available quality, runs key+energy analysis,
imports to iTunes, and adds to specified playlists. Fully unattended.

Usage:
  python3 ~/Music/DJ\ Downloads/slsk_batch_download.py \
    --playlist "Afro House" \
    "Mina Kupenda extended" \
    "Jamaican Bam Bam extended" \
    "Tom Brook Akwa Ogazi extended"

  # Or from a file (one search query per line):
  python3 ~/Music/DJ\ Downloads/slsk_batch_download.py \
    --playlist "Afro House" \
    --from-file ~/Music/DJ\ Downloads/download_list.txt

Options:
  --playlist NAME    iTunes playlist to add tracks to (can repeat for multiple)
  --from-file PATH   Text file with one search query per line
  --no-analyze       Skip key+energy analysis
  --no-import        Skip iTunes import
"""

import sys, os, time, subprocess, shutil, argparse, glob

# ── Paths ──────────────────────────────────────────────────────────────────
HOME            = os.path.expanduser("~")
COMPLETE_DIR    = os.path.join(HOME, "Soulseek Downloads", "complete")
DOWNLOADING_DIR = os.path.join(HOME, "Soulseek Downloads", "downloading")
DJ_DIR          = os.path.expanduser(os.environ.get("DJ_DIR", os.path.join(HOME, "Music", "DJ Downloads")))
# Resolve next to THIS file, not inside DJ_DIR, so a clone runs from anywhere.
ANALYZE_SCRIPT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyze_tracks.py")
CLICLICK        = "/opt/homebrew/bin/cliclick"
SUPPORTED_EXT   = {".mp3", ".wav", ".flac", ".aiff"}

# ── Helpers ────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip()

def snapshot_complete():
    """Return set of all files currently in the complete folder."""
    files = set()
    if os.path.isdir(COMPLETE_DIR):
        for root, _, fnames in os.walk(COMPLETE_DIR):
            for f in fnames:
                if not f.startswith('.'):
                    files.add(os.path.join(root, f))
    return files

# ── Soulseek steps ─────────────────────────────────────────────────────────
def close_all_searches():
    """Close all existing search tabs to keep the UI path clean."""
    out, err = osa('''
tell application "SoulseekQt" to activate
delay 0.5
tell application "System Events"
  tell process "SoulseekQt"
    tell window 1
      click radio button "Search" of tab group 1
      delay 0.3
      -- Close All Searches is in the top toolbar (group 1, not tab group)
      try
        set g to group 1
        set els to UI elements of g
        repeat with el in els
          try
            if description of el contains "Close" then
              click el
              delay 0.5
              exit repeat
            end if
          end try
        end repeat
      end try
    end tell
  end tell
end tell
''')

def do_search(query):
    """Type a search query, hit Search, then click the newly created tab."""
    safe_query = query.replace('"', '\\"')
    out, err = osa(f'''
tell application "SoulseekQt" to activate
delay 0.3
tell application "System Events"
  tell process "SoulseekQt"
    tell window 1
      click radio button "Search" of tab group 1
      delay 0.3
      set tf to text field 1 of combo box 1 of group 1 of group 1
      set focused of tf to true
      delay 0.2
      set value of tf to "{safe_query}"
      click button "Search" of group 1 of group 1
      delay 1
      -- Click the last radio button (newest search tab) to ensure it's selected
      set outerSG to splitter group 1 of group 1 of group 1
      set tabEl to item 2 of (UI elements of outerSG)
      set tabEls to UI elements of tabEl
      set lastRB to missing value
      repeat with el in tabEls
        if class of el is radio button then set lastRB to el
      end repeat
      if lastRB is not missing value then click lastRB
    end tell
  end tell
end tell
''')
    log(f"  → Searching: {query}")
    time.sleep(8)  # wait for results to populate

def find_and_download():
    """Find the first file row in results and trigger download. Returns filename or None."""
    out, err = osa(f'''
tell application "SoulseekQt" to activate
delay 0.3
tell application "System Events"
  tell process "SoulseekQt"
    tell window 1
      set outerSG to splitter group 1 of group 1 of group 1
      set innerGroup to item 1 of (UI elements of outerSG)
      set innerSG to splitter group 1 of innerGroup
      set theOutline to outline 1 of innerSG
      set rowCount to count of rows of theOutline
      set cx to 0
      set cy to 0
      set foundTitle to ""
      repeat with i from 1 to rowCount
        if cx > 0 then exit repeat
        set theRow to row i of theOutline
        repeat with el in UI elements of theRow
          try
            set t to title of el
            if t is not missing value then
              set tl to do shell script "echo " & quoted form of t & " | tr A-Z a-z"
              if tl ends with ".mp3" or tl ends with ".wav" or tl ends with ".flac" or tl ends with ".aiff" then
                set p to position of theRow
                set s to size of theRow
                set cx to round ((item 1 of p) + ((item 1 of s) / 2))
                set cy to round ((item 2 of p) + ((item 2 of s) / 2))
                set foundTitle to t
                exit repeat
              end if
            end if
          end try
        end repeat
      end repeat
      if cx > 0 then
        set menuX to cx + 116
        set menuY to cy + 8
        do shell script "{CLICLICK} rc:" & cx & "," & cy & " w:700 c:" & menuX & "," & menuY
        return foundTitle
      else
        return "NORESULTS:" & rowCount
      end if
    end tell
  end tell
end tell
''')
    return out if out and not out.startswith("NORESULTS") else None

def wait_for_download(before_snap, timeout=300):
    """Wait until a new file appears in complete/. Returns path or None."""
    log(f"  → Waiting for download (up to {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        current = snapshot_complete()
        new = current - before_snap
        if new:
            f = sorted(new)[0]  # take first new file
            log(f"  → Download complete: {os.path.basename(f)}")
            return f
        time.sleep(4)
    return None

# ── Post-download steps ────────────────────────────────────────────────────
def analyze(filepath):
    """Run key+energy analysis, writing to comment tag."""
    log(f"  → Analyzing: {os.path.basename(filepath)}")
    result = subprocess.run(
        ["python3", ANALYZE_SCRIPT, "--force", filepath],
        capture_output=True, text=True
    )
    # Extract the "→ Xkey Energy Y" line
    for line in result.stdout.splitlines():
        if "Energy" in line and "→" in line:
            log(f"  → Tagged: {line.strip()}")
            break
    if result.returncode != 0:
        log(f"  ⚠ Analysis error: {result.stderr[:200]}")

def import_to_itunes(filepath, playlists):
    """Copy to DJ Downloads and import to iTunes + given playlists."""
    dest = os.path.join(DJ_DIR, os.path.basename(filepath))
    if os.path.abspath(filepath) != os.path.abspath(dest):
        shutil.copy2(filepath, dest)
    log(f"  → Importing to iTunes: {os.path.basename(dest)}")

    playlist_adds = "\n".join(
        [f'duplicate theTrack to playlist "{pl}"' for pl in playlists]
    )
    out, err = osa(f'''
tell application "Music"
  set f to POSIX file "{dest}"
  set theTrack to (add f)
  {playlist_adds}
  return (name of theTrack) & " — " & (comment of theTrack)
end tell
''')
    if out:
        log(f"  ✅ {out}")
    elif err:
        log(f"  ⚠ iTunes error: {err[:200]}")

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Autonomous Soulseek batch downloader")
    parser.add_argument("queries", nargs="*", help="Search queries")
    parser.add_argument("--playlist", action="append", default=[], metavar="NAME",
                        help="iTunes playlist name (repeat for multiple)")
    parser.add_argument("--from-file", metavar="PATH",
                        help="Text file with one query per line")
    parser.add_argument("--no-analyze", action="store_true",
                        help="Skip key+energy analysis")
    parser.add_argument("--no-import", action="store_true",
                        help="Skip iTunes import")
    args = parser.parse_args()

    queries = list(args.queries)
    if args.from_file:
        path = os.path.expanduser(args.from_file)
        with open(path) as fh:
            queries += [l.strip() for l in fh if l.strip() and not l.startswith("#")]

    if not queries:
        print(__doc__)
        sys.exit(1)

    playlists = args.playlist or []
    # Always add to Claimed Songs
    if "Claimed Songs" not in playlists:
        playlists = ["Claimed Songs"] + playlists

    log(f"Starting batch download: {len(queries)} track(s)")
    log(f"Playlists: {', '.join(playlists)}")
    print()

    success, failed = [], []

    for i, query in enumerate(queries, 1):
        log(f"[{i}/{len(queries)}] {query}")
        before = snapshot_complete()
        do_search(query)
        filename = find_and_download()

        if not filename:
            log(f"  ✗ No results — skipping")
            failed.append(query)
            continue

        log(f"  → Found: {filename}")
        new_file = wait_for_download(before, timeout=300)

        if not new_file:
            log(f"  ✗ Download timed out — skipping")
            failed.append(query)
            continue

        if not args.no_analyze:
            analyze(new_file)

        if not args.no_import:
            import_to_itunes(new_file, playlists)

        success.append((query, os.path.basename(new_file)))
        print()

    print("=" * 55)
    log(f"Done: {len(success)} downloaded, {len(failed)} failed")
    if success:
        print("\nDownloaded:")
        for q, f in success:
            print(f"  ✅ {q}  →  {f}")
    if failed:
        print("\nFailed (no results):")
        for q in failed:
            print(f"  ✗ {q}")

if __name__ == "__main__":
    main()
