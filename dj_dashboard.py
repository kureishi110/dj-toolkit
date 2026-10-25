#!/usr/bin/env python3
"""
DJ Library Dashboard
Generates a standalone HTML analytics dashboard from iTunes + Rekordbox data.

Usage:
    python3 dj_dashboard.py         # pulls everything automatically
    python3 dj_dashboard.py --open  # auto-open in browser after generating

Data sources (all automatic — no manual exports needed):
    - iTunes: full library, genres, play counts, date added
    - Rekordbox master.db: DJ play counts, BPM, key (via pyrekordbox)
    - Rekordbox networkAnalyze6.db: analysis status per track
"""

import sys
import json
import sqlite3
import subprocess
import csv
import os
import re
import webbrowser
from pathlib import Path
from datetime import datetime, date
from collections import Counter, defaultdict

# Data lives in the music folder (override with DJ_DIR); code lives here.
# Defaults match the original layout, so behaviour is unchanged in place.
BASE = Path(os.environ.get("DJ_DIR", Path.home() / "Music" / "DJ Downloads")).expanduser()
OUTPUT_PATH = BASE / "dj_dashboard.html"
LIBRARY_CACHE = BASE / "itunes_library.json"
RB_NETWORK_DB = Path.home() / "Library/Pioneer/rekordbox/networkAnalyze6.db"

# Rekordbox musical key ID → name
RB_KEY_MAP = {
    '0': None, '1': 'Am', '2': 'Em', '3': 'Bm', '4': 'F#m', '5': 'Dbm', '6': 'Abm',
    '7': 'Ebm', '8': 'Bbm', '9': 'Fm', '10': 'Cm', '11': 'Gm', '12': 'Dm',
    '13': 'C', '14': 'G', '15': 'D', '16': 'A', '17': 'E', '18': 'B',
    '19': 'Gb', '20': 'Db', '21': 'Ab', '22': 'Eb', '23': 'Bb', '24': 'F',
}


def pull_itunes_data():
    """Pull full library data from iTunes via AppleScript."""
    print("Pulling iTunes library data...", flush=True)
    script = '''
    tell application "Music"
        set output to ""
        set tlist to (get every track of library playlist 1)
        repeat with t in tlist
            set pc to played count of t
            set da to ""
            try
                set da to date added of t as string
            end try
            set lp to ""
            try
                set lp to played date of t as string
            end try
            set rt to rating of t
            set output to output & (name of t) & "|||" & (artist of t) & "|||" & (genre of t) & "|||" & pc & "|||" & da & "|||" & lp & "|||" & rt & "~~~"
        end repeat
        return output
    end tell
    '''
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=600)
    tracks = []
    for entry in result.stdout.strip().split('~~~'):
        if '|||' not in entry:
            continue
        parts = entry.strip().split('|||')
        if len(parts) < 7:
            continue
        try:
            play_count = int(parts[3]) if parts[3].strip().isdigit() else 0
            rating = int(parts[6]) if parts[6].strip().isdigit() else 0
        except:
            play_count = 0
            rating = 0
        tracks.append({
            'title': parts[0].strip(),
            'artist': parts[1].strip(),
            'genre': parts[2].strip() or 'Untagged',
            'play_count': play_count,
            'date_added': parts[4].strip(),
            'last_played': parts[5].strip(),
            'rating': rating,
        })
    print(f"  {len(tracks)} tracks loaded from iTunes.", flush=True)
    return tracks


def pull_playlist_membership():
    """Pull playlist membership for all tracks."""
    print("Pulling playlist membership...", flush=True)
    script = '''
    tell application "Music"
        set output to ""
        set skip_playlists to {"Library", "Music", "Podcasts", "iTunes U", "Audiobooks", "Purchased", "Genius", "Recently Added", "Recently Played", "Top 25 Most Played", "Downloaded"}
        set all_playlists to every playlist
        repeat with pl in all_playlists
            if (name of pl) is not in skip_playlists then
                try
                    set tlist to tracks of pl
                    set pname to name of pl
                    repeat with t in tlist
                        set output to output & pname & "|||" & (name of t) & "|||" & (artist of t) & "~~~"
                    end repeat
                end try
            end if
        end repeat
        return output
    end tell
    '''
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=600)
    playlist_tracks = defaultdict(list)
    track_playlists = defaultdict(list)
    for entry in result.stdout.strip().split('~~~'):
        if '|||' not in entry:
            continue
        parts = entry.strip().split('|||')
        if len(parts) < 3:
            continue
        pl, title, artist = parts[0].strip(), parts[1].strip(), parts[2].strip()
        key = f"{artist}|{title}"
        playlist_tracks[pl].append(key)
        track_playlists[key].append(pl)
    print(f"  {len(playlist_tracks)} playlists found.", flush=True)
    return dict(playlist_tracks), dict(track_playlists)


def pull_rekordbox_plays():
    """
    Pull DJ play counts, BPM, key directly from Rekordbox master.db via pyrekordbox.
    Returns (tracks_with_plays, all_rb_tracks, genre_play_counts).
    """
    try:
        from pyrekordbox.db6 import Rekordbox6Database
    except ImportError:
        print("  pyrekordbox not installed (pip3 install pyrekordbox)", flush=True)
        return [], [], Counter()

    print("Pulling Rekordbox play data from master.db...", flush=True)
    try:
        db = Rekordbox6Database()
        rb_tracks = list(db.get_content())

        def _rel_name(obj, attr):
            try:
                rel = getattr(obj, attr, None)
                return rel.Name if rel else None
            except:
                return None

        all_rb = []
        genre_plays = Counter()
        for t in rb_tracks:
            genre = _rel_name(t, 'Genre') or ''
            bpm = round(t.BPM / 100, 1) if t.BPM else None
            key = RB_KEY_MAP.get(str(t.KeyID)) if t.KeyID else None
            plays = t.DJPlayCount or 0
            row = {
                'title': t.Title or '',
                'artist': _rel_name(t, 'Artist') or '',
                'genre': genre,
                'bpm': bpm,
                'key': key,
                'play_count': plays,
                'rating': t.Rating or 0,
                'analysed': bool(t.Analysed),
                'path': t.FolderPath or '',
            }
            all_rb.append(row)
            if plays > 0:
                genre_plays[genre or 'Unknown'] += plays

        played = sorted([r for r in all_rb if r['play_count'] > 0],
                        key=lambda x: x['play_count'], reverse=True)
        print(f"  {len(all_rb)} RB tracks | {len(played)} with play data", flush=True)
        return played, all_rb, genre_plays

    except Exception as e:
        print(f"  Rekordbox DB error: {e}", flush=True)
        return [], [], Counter()


def pull_rekordbox_analysis():
    """Pull analysis status from networkAnalyze6.db (fast, no encryption)."""
    if not RB_NETWORK_DB.exists():
        return {}
    print("Pulling Rekordbox analysis status...", flush=True)
    rb_data = {}
    try:
        conn = sqlite3.connect(str(RB_NETWORK_DB))
        rows = conn.execute(
            "SELECT SongFilePath, AnalyzeStatus FROM manage_tbl"
        ).fetchall()
        conn.close()
        for path, status in rows:
            fname = Path(path).stem.lower()
            rb_data[fname] = {'analyzed': status == 1}
        print(f"  {len(rb_data)} analysis records.", flush=True)
    except Exception as e:
        print(f"  networkAnalyze6.db error: {e}", flush=True)
    return rb_data


def build_genre_stats(tracks):
    genre_counts = Counter(t['genre'] for t in tracks)
    genre_plays = Counter()
    for t in tracks:
        genre_plays[t['genre']] += t['play_count']
    return genre_counts, genre_plays


def build_artist_stats(tracks):
    artist_counts = Counter()
    artist_plays = Counter()
    for t in tracks:
        artists = [a.strip() for a in re.split(r',|feat\.|ft\.', t['artist'], flags=re.IGNORECASE)]
        for a in artists:
            if a:
                artist_counts[a] += 1
                artist_plays[a] += t['play_count']
    return artist_counts, artist_plays


def build_timeline(tracks):
    """Group tracks by month added."""
    monthly = defaultdict(int)
    for t in tracks:
        da = t['date_added']
        if not da or da == 'missing value':
            continue
        try:
            # AppleScript date format: "Sunday, April 19, 2026 at 10:00:00 AM"
            dt = datetime.strptime(da, "%A, %B %d, %Y at %I:%M:%S %p")
            key = dt.strftime("%Y-%m")
            monthly[key] += 1
        except:
            try:
                # try other formats
                parts = da.split(',')
                if len(parts) >= 3:
                    month_day = parts[1].strip()
                    year = parts[2].strip().split(' ')[0]
                    dt = datetime.strptime(f"{month_day}, {year}", "%B %d, %Y")
                    monthly[dt.strftime("%Y-%m")] += 1
            except:
                pass
    return dict(sorted(monthly.items()))


def rb_artist_plays_from_tracks(rb_played):
    """Aggregate play counts by artist from Rekordbox played tracks."""
    artist_plays = Counter()
    for t in rb_played:
        for a in re.split(r',\s*|feat\.|ft\.', t['artist'], flags=re.IGNORECASE):
            a = a.strip()
            if a:
                artist_plays[a] += t['play_count']
    return artist_plays


def to_js(obj):
    return json.dumps(obj, ensure_ascii=False)


def _li_track(title, artist, genre, plays):
    return (
        '<li><div class="track-info">'
        '<div class="track-title">' + _esc(title) + '</div>'
        '<div class="track-artist">' + _esc(artist) +
        '<span class="genre-tag">' + _esc(genre) + '</span></div>'
        '</div><div class="track-meta">' + str(plays) + '×</div></li>'
    )

def _li_rb(key_str, count):
    parts = key_str.split(' \u2014 ', 1)
    artist = parts[0] if len(parts) > 1 else ''
    title = parts[1] if len(parts) > 1 else key_str
    return (
        '<li><div class="track-info">'
        '<div class="track-title">' + _esc(title) + '</div>'
        '<div class="track-artist">' + _esc(artist) + '</div>'
        '</div><div class="track-meta">' + str(count) + '×</div></li>'
    )

def _li_artist(artist, count):
    return (
        '<li><div class="track-info">'
        '<div class="track-title">' + _esc(artist) + '</div>'
        '</div><div class="track-meta">' + str(count) + '×</div></li>'
    )

def _li_multi(key, playlists):
    parts = key.split('|', 1)
    artist = parts[0] if len(parts) > 1 else ''
    title = parts[1] if len(parts) > 1 else key
    pl_tags = ''.join('<span class="pl-tag">' + _esc(pl) + '</span>' for pl in playlists)
    return (
        '<li>'
        '<div class="track-title">' + _esc(title) + '</div>'
        '<div class="track-artist">' + _esc(artist) + '</div>'
        '<div class="pl-tags">' + pl_tags + '</div>'
        '</li>'
    )

def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def generate_html(tracks, playlist_tracks, track_playlists, rb_analysis,
                  rb_played, rb_genre_plays):
    genre_counts, _ = build_genre_stats(tracks)
    artist_counts, _ = build_artist_stats(tracks)
    timeline = build_timeline(tracks)
    rb_artist_plays = rb_artist_plays_from_tracks(rb_played)

    top_genres = genre_counts.most_common(20)
    top_artists_by_count = artist_counts.most_common(20)
    top_playlists = sorted(playlist_tracks.items(), key=lambda x: len(x[1]), reverse=True)[:20]

    # Most played via iTunes
    itunes_played = sorted([t for t in tracks if t['play_count'] > 0],
                           key=lambda x: x['play_count'], reverse=True)[:30]

    # Rekordbox top plays
    rb_top_tracks = [(f"{t['artist']} — {t['title']}", t['play_count']) for t in rb_played[:30]]
    rb_top_artists = rb_artist_plays.most_common(20)
    rb_genre_top = sorted(rb_genre_plays.items(), key=lambda x: x[1], reverse=True)

    # Analysis status
    analyzed_count = sum(1 for v in rb_analysis.values() if v['analyzed'])

    # Genre color palette
    COLORS = [
        '#FF6B6B','#4ECDC4','#45B7D1','#96CEB4','#FFEAA7','#DDA0DD','#98D8C8',
        '#F7B267','#A8E6CF','#FFD93D','#6BCB77','#4D96FF','#FF6FC8','#C77DFF',
        '#FF9A3C','#00B4D8','#E9C46A','#F4A261','#E76F51','#2A9D8F'
    ]

    genre_labels = [g for g, _ in top_genres]
    genre_values = [c for _, c in top_genres]

    artist_labels = [a for a, _ in top_artists_by_count]
    artist_values = [c for _, c in top_artists_by_count]

    playlist_labels = [p for p, _ in top_playlists]
    playlist_values = [len(t) for _, t in top_playlists]

    timeline_labels = list(timeline.keys())
    timeline_values = list(timeline.values())

    # Multi-playlist tracks
    multi_playlist = sorted(
        [(k, v) for k, v in track_playlists.items() if len(v) >= 2],
        key=lambda x: len(x[1]), reverse=True
    )[:20]

    now_str = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # Pre-build HTML fragments (avoid nested f-string issues in Python 3.9)
    total_rb_plays = sum(t['play_count'] for t in rb_played)
    rb_stat = (
        '<div class="stat"><div class="num">' + str(total_rb_plays) +
        '</div><div class="label">RB DJ Plays</div></div>'
    ) if rb_played else ''

    rb_note = '' if rb_played else (
        '<div class="rb-note">⚠️ Could not read Rekordbox master.db. '
        'Make sure Rekordbox is installed and pyrekordbox is available.</div>'
    )

    multi_no_data = "<p class='no-data'>No tracks in multiple playlists yet.</p>" if not multi_playlist else ''
    multi_html = ''.join(_li_multi(k, v) for k, v in multi_playlist)

    itunes_section = ''
    if itunes_played:
        rows = ''.join(_li_track(t['title'], t['artist'], t['genre'], t['play_count'])
                       for t in itunes_played[:20])
        itunes_section = (
            '<div class="section-title">iTunes Play Data</div>'
            '<div class="card" style="margin-bottom: 24px;">'
            '<h2>Most Played Tracks <span class="accent">via iTunes</span></h2>'
            '<ul class="track-list">' + rows + '</ul></div>'
        )

    # Pre-build RB genre chart JS (avoids f-string nesting issues)
    if rb_played and rb_genre_top:
        rb_genre_labels_js = to_js([g for g, _ in rb_genre_top])
        rb_genre_values_js = to_js([v for _, v in rb_genre_top])
        rb_genre_chart_js = (
            "new Chart(document.getElementById('rbGenreBar'), {"
            "type: 'bar',"
            "data: { labels: " + rb_genre_labels_js + ","
            "datasets: [{ data: " + rb_genre_values_js + ","
            "backgroundColor: '#FF6B6B', borderRadius: 6, borderSkipped: false }] },"
            "options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,"
            "plugins: { legend: { display: false },"
            "tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.x} plays` } } },"
            "scales: { x: { grid: { color: '#252540' }, ticks: { color: '#888' } },"
            "y: { grid: { display: false }, ticks: { color: '#ccc', font: { size: 11 } } } }"
            "} });"
        )
    else:
        rb_genre_chart_js = '// No RB play data'

    rb_section = ''
    if rb_played:
        track_rows = ''.join(_li_rb(k, v) for k, v in rb_top_tracks[:20])
        artist_rows = ''.join(_li_artist(a, v) for a, v in rb_top_artists[:20])
        rb_section = (
            '<div class="section-title">What You Actually Play (Rekordbox)</div>'
            '<div class="grid-2" style="margin-bottom:24px">'
            '<div class="card">'
            '<h2>Genres You Play Most <span class="accent">by DJ play count</span></h2>'
            '<div class="chart-wrap"><canvas id="rbGenreBar"></canvas></div>'
            '</div>'
            '<div class="card"><h2>Most Played Artists <span class="accent">in Rekordbox</span></h2>'
            '<ul class="track-list">' + artist_rows + '</ul></div>'
            '</div>'
            '<div class="card" style="margin-bottom:24px">'
            '<h2>Most Played Tracks <span class="accent">in Rekordbox</span></h2>'
            '<ul class="track-list">' + track_rows + '</ul></div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tanya's DJ Library Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0e0e14;
    color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }}
  .header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    padding: 40px 40px 30px;
    border-bottom: 1px solid #2a2a4a;
  }}
  .header h1 {{ font-size: 28px; font-weight: 700; color: #fff; letter-spacing: -0.5px; }}
  .header .subtitle {{ color: #8888aa; font-size: 13px; margin-top: 6px; }}
  .stat-bar {{
    display: flex; gap: 20px; margin-top: 24px; flex-wrap: wrap;
  }}
  .stat {{
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 14px 20px;
    min-width: 120px;
  }}
  .stat .num {{ font-size: 28px; font-weight: 700; color: #fff; }}
  .stat .label {{ font-size: 11px; color: #8888aa; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }}
  .main {{ padding: 30px 40px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; margin-bottom: 24px; }}
  @media (max-width: 900px) {{
    .grid-2, .grid-3 {{ grid-template-columns: 1fr; }}
  }}
  .card {{
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 14px;
    padding: 24px;
  }}
  .card h2 {{
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #8888aa;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .card h2 .accent {{ color: #4ECDC4; }}
  .chart-wrap {{ position: relative; height: 260px; }}
  .chart-wrap-tall {{ position: relative; height: 340px; }}
  .track-list {{ list-style: none; }}
  .track-list li {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid #252540;
    gap: 12px;
  }}
  .track-list li:last-child {{ border-bottom: none; }}
  .track-info {{ flex: 1; min-width: 0; }}
  .track-title {{ font-weight: 500; color: #e0e0e0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .track-artist {{ font-size: 12px; color: #8888aa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .track-meta {{ font-size: 12px; color: #4ECDC4; white-space: nowrap; font-weight: 600; }}
  .genre-tag {{
    display: inline-block; padding: 2px 8px; border-radius: 20px;
    font-size: 11px; font-weight: 500; margin-left: 8px;
    background: rgba(78,205,196,0.15); color: #4ECDC4;
  }}
  .no-data {{ color: #666; font-style: italic; padding: 20px 0; }}
  .rb-note {{
    background: rgba(255,107,107,0.08);
    border: 1px solid rgba(255,107,107,0.2);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 12px;
    color: #FF6B6B;
    margin-bottom: 24px;
  }}
  .rb-note strong {{ color: #ff9999; }}
  .multi-playlist-list {{ list-style: none; }}
  .multi-playlist-list li {{
    padding: 8px 0;
    border-bottom: 1px solid #252540;
  }}
  .multi-playlist-list li:last-child {{ border-bottom: none; }}
  .pl-tags {{ margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; }}
  .pl-tag {{
    display: inline-block; padding: 1px 7px; border-radius: 20px;
    font-size: 10px;
    background: rgba(150,206,180,0.15); color: #96CEB4;
    border: 1px solid rgba(150,206,180,0.2);
  }}
  .section-title {{
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #555577;
    margin: 32px 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid #252540;
  }}
</style>
</head>
<body>
<div class="header">
  <h1>🎧 DJ Library Dashboard</h1>
  <div class="subtitle">Generated {now_str} · iTunes + Rekordbox</div>
  <div class="stat-bar">
    <div class="stat"><div class="num">{len(tracks)}</div><div class="label">Total Tracks</div></div>
    <div class="stat"><div class="num">{len(genre_counts)}</div><div class="label">Genres</div></div>
    <div class="stat"><div class="num">{len(playlist_tracks)}</div><div class="label">Playlists</div></div>
    <div class="stat"><div class="num">{len(artist_counts)}</div><div class="label">Artists</div></div>
    <div class="stat"><div class="num">{analyzed_count}</div><div class="label">RB Analyzed</div></div>
    <div class="stat"><div class="num">{sum(t['play_count'] for t in tracks)}</div><div class="label">iTunes Plays</div></div>
    {rb_stat}
  </div>
</div>

<div class="main">

{rb_note}

<div class="section-title">Library Composition</div>
<div class="grid-2">
  <div class="card">
    <h2>Genre Breakdown <span class="accent">by track count</span></h2>
    <div class="chart-wrap">
      <canvas id="genrePie"></canvas>
    </div>
  </div>
  <div class="card">
    <h2>Tracks per Genre</h2>
    <div class="chart-wrap">
      <canvas id="genreBar"></canvas>
    </div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Top Artists <span class="accent">by track count</span></h2>
    <div class="chart-wrap-tall">
      <canvas id="artistBar"></canvas>
    </div>
  </div>
  <div class="card">
    <h2>Playlist Sizes</h2>
    <div class="chart-wrap-tall">
      <canvas id="playlistBar"></canvas>
    </div>
  </div>
</div>

<div class="section-title">Library Growth</div>
<div class="card" style="margin-bottom: 24px;">
  <h2>Tracks Added Over Time</h2>
  <div class="chart-wrap">
    <canvas id="timeline"></canvas>
  </div>
</div>

<div class="section-title">Versatility</div>
<div class="card" style="margin-bottom: 24px;">
  <h2>Tracks in Multiple Playlists <span class="accent">genre-crossover tracks</span></h2>
  {multi_no_data}
  <ul class="multi-playlist-list">{multi_html}</ul>
</div>

{itunes_section}

{rb_section}

</div>

<script>
const COLORS = {to_js(COLORS)};

// Genre Pie
new Chart(document.getElementById('genrePie'), {{
  type: 'doughnut',
  data: {{
    labels: {to_js(genre_labels)},
    datasets: [{{ data: {to_js(genre_values)}, backgroundColor: COLORS, borderWidth: 0, hoverOffset: 8 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'right', labels: {{ color: '#aaa', font: {{ size: 11 }}, boxWidth: 12, padding: 8 }} }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.label}}: ${{ctx.parsed}} tracks` }} }}
    }}
  }}
}});

// Genre Bar
new Chart(document.getElementById('genreBar'), {{
  type: 'bar',
  data: {{
    labels: {to_js(genre_labels)},
    datasets: [{{ data: {to_js(genre_values)}, backgroundColor: COLORS, borderRadius: 6, borderSkipped: false }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.x}} tracks` }} }} }},
    scales: {{
      x: {{ grid: {{ color: '#252540' }}, ticks: {{ color: '#888' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#ccc', font: {{ size: 11 }} }} }}
    }}
  }}
}});

// Artist Bar
new Chart(document.getElementById('artistBar'), {{
  type: 'bar',
  data: {{
    labels: {to_js(artist_labels)},
    datasets: [{{ data: {to_js(artist_values)}, backgroundColor: '#4ECDC4', borderRadius: 4, borderSkipped: false }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.x}} tracks` }} }} }},
    scales: {{
      x: {{ grid: {{ color: '#252540' }}, ticks: {{ color: '#888' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#ccc', font: {{ size: 10 }} }} }}
    }}
  }}
}});

{rb_genre_chart_js}

// Playlist Bar
new Chart(document.getElementById('playlistBar'), {{
  type: 'bar',
  data: {{
    labels: {to_js(playlist_labels)},
    datasets: [{{ data: {to_js(playlist_values)}, backgroundColor: '#FF6B6B', borderRadius: 4, borderSkipped: false }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.x}} tracks` }} }} }},
    scales: {{
      x: {{ grid: {{ color: '#252540' }}, ticks: {{ color: '#888' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#ccc', font: {{ size: 10 }} }} }}
    }}
  }}
}});

// Timeline
new Chart(document.getElementById('timeline'), {{
  type: 'bar',
  data: {{
    labels: {to_js(timeline_labels)},
    datasets: [{{
      label: 'Tracks Added',
      data: {to_js(timeline_values)},
      backgroundColor: 'rgba(69,183,209,0.6)',
      borderColor: '#45B7D1',
      borderWidth: 1,
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.y}} tracks added` }} }} }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#888' }} }},
      y: {{ grid: {{ color: '#252540' }}, ticks: {{ color: '#888' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""
    return html


def main():
    args = sys.argv[1:]
    auto_open = '--open' in args

    print("\n=== DJ Dashboard Generator ===\n")

    tracks = pull_itunes_data()
    playlist_tracks, track_playlists = pull_playlist_membership()
    rb_analysis = pull_rekordbox_analysis()
    rb_played, rb_all, rb_genre_plays = pull_rekordbox_plays()

    print("\nGenerating dashboard...", flush=True)
    html = generate_html(tracks, playlist_tracks, track_playlists, rb_analysis,
                         rb_played, rb_genre_plays)

    OUTPUT_PATH.write_text(html, encoding='utf-8')
    print(f"\nDashboard saved: {OUTPUT_PATH}")

    if auto_open:
        webbrowser.open(f"file://{OUTPUT_PATH}")
        print("Opened in browser.")
    else:
        print(f"Open with: open '{OUTPUT_PATH}'")

    print("\nDone.")


if __name__ == '__main__':
    main()
