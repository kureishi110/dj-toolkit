# dj-toolkit

Command-line tooling for building and maintaining a DJ library on macOS: source a
track, prove it isn't a transcode, derive its Camelot key and energy, tag it, and
land it in Apple Music where Rekordbox will pick it up.

Written for my own library (Nu Disco / Melodic House / R&B, XDJ-RR, Rekordbox).
It is a real working pipeline rather than a polished product, so the defaults are
mine. Paths are overridable with `DJ_DIR`.

## The one command

```bash
python3 dj_grab.py tracks.csv
python3 dj_grab.py tracks.csv --playlist "Melodic House"
```

`tracks.csv` is two columns, `Artist,Title`. From there `dj_grab.py` runs the
whole chain and everything else in this repo is a stage of it you can also run
alone:

1. **Dedupe** against Apple Music over AppleScript, so you don't re-buy what you own
2. **Download** via [`sldl`](https://github.com/fiso64/slsk-batchdl) into a fresh timestamped temp dir
3. **Quality gate** — `check_quality.py` renders a spectrogram and reads the frequency cutoff
4. **Analyze** — `analyze_tracks.py` writes Camelot key + energy into the comment tag
5. **Import** — `import_to_itunes.py` adds to Apple Music one file at a time
6. **Classify** — artist-map first, file genre tag as fallback; a track can land in several playlists

## What each script does

| Script | Does |
|---|---|
| `dj_grab.py` | The pipeline above. The only command you normally type. |
| `check_quality.py` | Spectrogram + cutoff analysis. Catches MP3s upconverted to fake FLAC. |
| `analyze_tracks.py` | Camelot key + 1–10 energy into the comment tag. Replaces Mixed In Key. |
| `import_to_itunes.py` | AppleScript import that survives special characters in filenames. |
| `resolve_spotify.py` | Spotify track URLs → `Artist,Title` CSV via the embed endpoint. No credentials needed. |
| `dj_dashboard.py` | Standalone HTML dashboard: genre split, top artists, playlist sizes, growth. |
| `recover_batches.py` | Sweeps historical download folders for tracks that never made it into the library. |
| `slsk_batch_download.py` | Older unattended downloader. Superseded by `dj_grab.py`; kept because it still works. |

## The two parts worth stealing

**Transcode detection.** A 320kbps MP3 re-encoded as FLAC looks lossless in every
tag and sounds wrong on a loud system. `check_quality.py` renders the spectrogram
and reads where the frequency content actually stops:

| Cutoff | Verdict |
|---|---|
| 18kHz+ | pass |
| 16–18kHz | warn — probably a 256kbps source |
| below 14kHz | reject |

**Energy scoring.** Mixed In Key charges for a number that drives every set I
build. `analyze_tracks.py` approximates it from four weighted signals, then writes
`"8A Energy 7"` into the comment tag where Rekordbox shows it in the Comment column:

| Signal | Weight | Why |
|---|---|---|
| Loudness (EBU R128) | 30% | raw punch |
| Danceability | 25% | rhythmic coherence |
| Onset density | 25% | how busy and driving it is |
| Bass energy ratio | 20% | kick and sub presence |

The weights are tuned by ear against tracks I already had Mixed In Key numbers
for. They are opinionated, not authoritative.

## Setup

```bash
pip3 install -r requirements.txt
brew install ffmpeg cliclick
```

`sldl` is a separate binary — see [slsk-batchdl](https://github.com/fiso64/slsk-batchdl).
It reads its own config at `~/.sldl.conf`, which holds Soulseek credentials and is
gitignored here. Nothing in this repo stores a credential.

Data and code are separate. Scripts read and write the music folder, which
defaults to `~/Music/DJ Downloads` and follows `DJ_DIR` if you set it:

```bash
export DJ_DIR="$HOME/Music/DJ Downloads"
```

So the repo can live anywhere; a clone won't write library caches into itself.

## `skill/dj.md`

The Claude Code skill I use to drive all of this conversationally — paste a
screenshot of a tracklist, get a dedupe summary and one command to run. Drop it in
`~/.claude/commands/dj.md`. It encodes the rules that took a while to learn, the
main one being that `sldl` locks a port, so the agent writes the CSV and hands the
command over rather than running it.

## Caveats

- macOS only. The import path is AppleScript against Apple Music.
- `essentia` is a hard dependency of `analyze_tracks.py` and can be a fight to install.
- The artist → playlist map in `dj_grab.py` is my genres. Edit it or it will guess wrong for you.
- Soulseek is a peer-to-peer network. What you download on it is on you.

## License

MIT
