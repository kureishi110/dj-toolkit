#!/usr/bin/env python3
"""
dj_grab.py — One-command DJ download pipeline.

Usage:
  python3 ~/Music/DJ\ Downloads/dj_grab.py tracks.csv

Steps:
  1. Check iTunes — skip tracks already in library
  2. Download via sldl to a fresh temp dir
  3. Quality check new files only (delete WARNs/REJECTs)
  4. Key + energy analysis
  5. Import to iTunes
  6. Add every track to "Claimed Songs" + auto-detected genre playlist
"""

import os, sys, csv, subprocess, time, argparse
from pathlib import Path

HOME     = Path.home()
DJ_DIR   = Path(os.environ.get("DJ_DIR", HOME / "Music" / "DJ Downloads")).expanduser()
SLDL     = Path(os.environ.get("SLDL_BIN", HOME / "bin" / "sldl")).expanduser()
SLDL_CFG = Path(os.environ.get("SLDL_CONF", HOME / ".sldl.conf")).expanduser()

# Sibling scripts resolve next to THIS file, not inside DJ_DIR, so a clone runs
# from wherever it sits.
HERE = Path(__file__).resolve().parent

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aiff", ".m4a"}

# ── Artist → playlists map ─────────────────────────────────────────────────
# Primary classification source. Tracks go in ALL matching playlists.
# Genre tags from files are unreliable — artist knowledge overrides them.
ARTIST_MAP = [
    # Melodic House + Progressive House crossovers
    (["rüfüs du sol", "rufus du sol", "lane 8", "ben böhmer", "tinlicker",
      "kidnap", "neon tide"],
     ["Melodic House", "Progressive House"]),

    # Melodic House leaning
    (["vandelux", "nu aspect", "eli & fur", "row", "samuel miller",
      "blonde maze", "mild minds", "alastair lane", "oskar med k",
      "innerverse", "lost in pacific", "daydream affiliate"],
     ["Melodic House"]),

    # Afro House / Afro leaning
    (["&me", "keinemusik", "black coffee"],
     ["Afro House", "Melodic House"]),

    # Indie Electronic leaning
    (["elderbrook", "bob moses", "bonobo", "bicep", "four tet",
      "fred again", "hozier"],
     ["Indie Electronic"]),

    # Melodic House + Indie Electronic crossovers
    (["eric sharp", "emmit fenn", "bunt.", "le youth"],
     ["Melodic House", "Indie Electronic"]),

    # Nu Disco
    (["disclosure", "duke dumont", "kaytranada", "daft punk"],
     ["Nu Disco"]),

    # Alt R&B
    (["the weeknd", "sza", "frank ocean", "daniel caesar", "serpentwithfeet"],
     ["Alt R&B"]),

    # R&B
    (["usher", "beyoncé", "mary j blige", "alicia keys"],
     ["R&B"]),

    # Indie Pop
    (["haux", "novo amor", "Gregory Alan Isakov"],
     ["Indie Pop"]),

    # Dance Pop / Desi — South Asian artists
    (["diljit dosanjh", "ap dhillon", "ikky", "j-statik", "farasat anees",
      "karan aujla", "talwiinder", "gunni", "jasmin walia", "simar",
      "tangan tribe", "dj amxn", "dj talv", "dj jiannu", "dj h",
      "mastani", "sargi maan", "uday shergill", "zzorawar", "tiësto"],
     ["Dance Pop"]),
]

# Fallback genre-tag → playlists (used only when artist not in ARTIST_MAP)
GENRE_TAG_MAP = [
    (["melodic house", "melodic techno"],   ["Melodic House"]),
    (["progressive house"],                 ["Progressive House"]),
    (["afro house"],                        ["Afro House", "Melodic House"]),
    (["tech house"],                        ["House"]),
    (["deep house", "house"],               ["House"]),
    (["nu disco", "disco"],                 ["Nu Disco"]),
    (["indie electronic", "electronica"],   ["Indie Electronic"]),
    (["indie pop"],                         ["Indie Pop"]),
    (["alt r&b"],                           ["Alt R&B"]),
    (["r&b", "soul"],                       ["R&B"]),
    (["dance pop"],                         ["Dance Pop"]),
    (["hip hop", "rap"],                    ["Hip Hop Pop"]),
]

def log(msg): print(f"  {msg}", flush=True)

# ── 1. iTunes dupe check ───────────────────────────────────────────────────

def itunes_has_track(title: str, artist: str) -> bool:
    safe_t = title.replace('"', '').replace("'", "")
    safe_a = artist.replace('"', '').replace("'", "")
    script = f'''
tell application "Music"
  set r to (search playlist "Library" for "{safe_t}" only songs)
  repeat with tr in r
    if (name of tr as string) contains "{safe_t}" and (artist of tr as string) contains "{safe_a}" then return "true"
  end repeat
  return "false"
end tell'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() == "true"

def filter_dupes(tracks):
    missing, found = [], []
    for t in tracks:
        (found if itunes_has_track(t["Title"], t["Artist"]) else missing).append(t)
    return missing, found

# ── 2. CSV ─────────────────────────────────────────────────────────────────

def write_csv(tracks, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Artist", "Title"])
        w.writeheader()
        w.writerows(tracks)

def load_csv(path):
    tracks = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and "Artist" in reader.fieldnames:
            for row in reader:
                tracks.append({"Artist": row["Artist"].strip(), "Title": row["Title"].strip()})
        else:
            f.seek(0)
            for line in f:
                parts = line.strip().split(",", 1)
                if len(parts) == 2:
                    tracks.append({"Artist": parts[0].strip(), "Title": parts[1].strip()})
    return tracks

# ── 3. sldl ────────────────────────────────────────────────────────────────

def run_sldl(csv_path, out_dir):
    cmd = [str(SLDL), str(csv_path), "--config", str(SLDL_CFG),
           "--path", str(out_dir), "--desperate"]
    print(f"\n  Running: {' '.join(cmd)}\n")
    subprocess.run(cmd)

def find_audio_dir(out_dir):
    """sldl drops files in a subfolder named after the CSV stem. Find it."""
    for sub in out_dir.iterdir():
        if sub.is_dir() and any(
            f.suffix.lower() in AUDIO_EXTS
            for f in sub.iterdir() if f.is_file()
        ):
            return sub
    return out_dir  # fallback: files in root (shouldn't happen but safe)

# ── 4. Quality check ──────────────────────────────────────────────────────

def run_quality_check(folder):
    print("\n  Quality check...")
    r = subprocess.run(
        ["python3", str(HERE / "check_quality.py"), str(folder)],
        capture_output=True, text=True
    )
    output = r.stdout + r.stderr
    issues = [l for l in output.splitlines()
              if any(tag in l for tag in ["WARN", "FLAG", "REJECT", "PASS", "Summary"])]
    for line in issues:
        log(line.strip())

# ── 5. Analyze ────────────────────────────────────────────────────────────

def run_analyze(folder):
    print("\n  Key + energy analysis...")
    subprocess.run(["python3", str(HERE / "analyze_tracks.py"), str(folder)])

# ── 6. Import ─────────────────────────────────────────────────────────────

def run_import(folder):
    print("\n  Importing to iTunes...")
    subprocess.run(["python3", str(HERE / "import_to_itunes.py"), str(folder)])

# ── 7. Genre classification + playlist assignment ─────────────────────────

def read_genre_tag(filepath):
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(filepath, easy=True)
        if audio and "genre" in audio:
            return audio["genre"][0].lower()
    except Exception:
        pass
    return ""

def classify_track(filepath, title, artist):
    """Return ALL matching playlists for this track.

    Priority: artist knowledge map → genre tag fallback → unknown.
    Tracks go in multiple playlists when they fit multiple genres.
    """
    artist_lower = artist.lower()
    playlists = []

    # 1. Artist map is primary — most reliable, overrides file genre tag
    for artists, pls in ARTIST_MAP:
        if any(a in artist_lower for a in artists):
            playlists = pls
            break

    # 2. If artist unknown, try the file's genre tag as a hint
    if not playlists and filepath:
        genre_tag = read_genre_tag(filepath)
        if genre_tag:
            for keywords, pls in GENRE_TAG_MAP:
                if any(k in genre_tag for k in keywords):
                    playlists = pls
                    break

    return playlists  # empty = unknown, goes to Claimed Songs only

def add_to_playlists(title, playlists):
    """Add a track (looked up by title) to the given iTunes playlists."""
    safe_title = title.replace('"', '').replace("'", "\\'")
    playlist_cmds = "\n".join(
        f'duplicate theTrack to playlist "{pl}"'
        for pl in playlists
    )
    script = f'''
tell application "Music"
  set results to (every track of library playlist 1 whose name contains "{safe_title}")
  if (count of results) > 0 then
    set theTrack to item 1 of results
    {playlist_cmds}
    return "ok"
  else
    return "not found"
  end if
end tell'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()

def assign_playlists(audio_dir, downloaded_tracks):
    """For each downloaded track: classify genre, add to Claimed Songs + genre playlist."""
    print("\n  Assigning playlists...")

    # Build a title→filepath map from what's in the audio dir
    files = [f for f in audio_dir.rglob("*") if f.suffix.lower() in AUDIO_EXTS]

    unmatched = []
    for track in downloaded_tracks:
        title  = track["Title"]
        artist = track["Artist"]

        # Find best matching file (loose match on title words)
        title_words = [w.lower() for w in title.split() if len(w) > 2]
        match = None
        for f in files:
            fname = f.stem.lower()
            if any(w in fname for w in title_words):
                match = f
                break

        genre_playlists = classify_track(match, title, artist) if match else []
        all_playlists   = ["Claimed Songs"] + genre_playlists

        result = add_to_playlists(title, all_playlists)
        status = "✓" if result == "ok" else "⚠ not found in iTunes"
        playlist_str = ", ".join(all_playlists) if genre_playlists else "Claimed Songs (genre unknown)"
        log(f"{status}  {artist} — {title}  →  {playlist_str}")

        if not genre_playlists:
            unmatched.append(f"{artist} — {title}")

    if unmatched:
        print(f"\n  ⚠ No genre detected for {len(unmatched)} track(s) — added to Claimed Songs only:")
        for t in unmatched:
            log(t)

# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="CSV file with Artist,Title columns")
    parser.add_argument("--skip-analyze", action="store_true")
    parser.add_argument("--skip-import", action="store_true")
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser()
    if not csv_path.exists():
        sys.exit(f"File not found: {csv_path}")

    tracks = load_csv(csv_path)
    if not tracks:
        sys.exit("No tracks found in CSV.")

    print(f"\n{'='*55}")
    print(f"  dj_grab — {len(tracks)} tracks in list")
    print(f"{'='*55}\n")

    # Step 1: iTunes dupe check
    print("  Checking iTunes library for existing tracks...")
    missing, found = filter_dupes(tracks)

    if found:
        print(f"\n  Already in library ({len(found)}) — skipping:")
        for t in found:
            log(f"✓ {t['Artist']} — {t['Title']}")

    if not missing:
        print("\n  Nothing to download. Done.")
        return

    print(f"\n  To download ({len(missing)}):")
    for t in missing:
        log(f"· {t['Artist']} — {t['Title']}")

    # Step 2: fresh temp dir + filtered CSV
    batch_name  = f"dj_batch_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir     = Path("/tmp") / batch_name
    out_dir.mkdir(parents=True, exist_ok=True)
    filtered_csv = out_dir / "tracks.csv"
    write_csv(missing, filtered_csv)

    # Step 3: download
    print()
    run_sldl(filtered_csv, out_dir)

    # Find actual audio subfolder sldl created
    audio_dir = find_audio_dir(out_dir)

    # Step 4: quality check
    run_quality_check(audio_dir)

    # Step 5: analyze
    if not args.skip_analyze:
        run_analyze(audio_dir)

    # Step 6: import
    if not args.skip_import:
        run_import(audio_dir)

    # Step 7: playlist assignment
    assign_playlists(audio_dir, missing)

    print(f"\n{'='*55}")
    print(f"  Done. Batch: {out_dir}")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    main()
