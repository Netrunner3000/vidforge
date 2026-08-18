# vidforge

Autonomously turns a topic into a finished, narrated, illustrated YouTube video:
script → voiceover → per-scene imagery → Ken Burns motion → burned-in captions →
music bed → thumbnail → metadata.

Also generates structured PowerPoint course presentations from topics or outlines.

There is a desktop app and a CLI. Both drive the same pipeline.

```bash
./build_app.sh                 # builds vidforge.app into /Applications
python app.py                  # or just run the GUI from the repo
python main.py run --topic "How undersea cables carry the entire internet"
```

## The app

`app.py` is a PySide6 desktop app with five tabs:

- **Produce** — type a topic (or leave it blank to take the next queued one), set
  length, visuals and voice, then watch it build. Live stage indicators, a
  progress bar and a streaming log. **Stop** halts cleanly at the next step, and
  everything finished so far is kept.
- **Library** — every video produced, with thumbnail, description, tags and
  chapters. Play it, show the files, resume an unfinished one, or upload it.
- **Scanner** — what is pulling views on YouTube right now, with velocity and
  engagement metrics, grouped into topic veins you can send to the queue. See
  [The Scanner](#the-scanner).
- **Topics** — the queue, editable in place, plus a "Suggest 10 more" button.
- **Settings** — channel identity, narrator voice, the public-upload switch, API
  key status and the same environment checks as `main.py doctor`.

`./build_app.sh` bundles it with PyInstaller and installs `/Applications/vidforge.app`.
The bundled build keeps settings, topics, keys and rendered videos in
`~/Library/Application Support/vidforge` (seeded on first launch) so a rebuild
never overwrites your data. Run from the repo instead and everything stays in the
project folder.

Everything lands in `output/<slug>/`:

```
20260802-1844-norways-giant-mirrors/
├── 20260802-1844-norways-giant-mirrors.mp4   1920x1080, 30fps, -14 LUFS
├── thumbnail.jpg                             1280x720
├── metadata.json                             title, description, tags, chapters
├── script.json                               the full plan
├── manifest.json                             per-stage state + cost estimate
├── audio/  images/  clips/  work/            intermediates (resumable)
```

## Setup

```bash
brew install ffmpeg uv
uv venv && uv pip install -r requirements.txt
cp .env.example .env      # add OPENAI_API_KEY
python main.py doctor
```

`doctor` checks ffmpeg filters, fonts, API keys and Python deps, and tells you
what's missing before you spend anything.

## CLI

| Command | What it does |
|---|---|
| `run` | Produce a video. `--topic`, `--count N`, `--resume SLUG`, `--seconds`, `--visuals`, `--provider` |
| `course` | Generate a PowerPoint course. `--topic "..."`, `--outline FILE`, `--output FILENAME`, `--open` |
| `scan` | Trending videos + topic veins. `--region`, `--category`, `--limit`, `--queue`, `--refresh`, `--json` |
| `topics` | Show the queue; `--suggest 10` generates new ideas, `--add "..."` appends |
| `list` | Everything produced so far |
| `upload SLUG` | Upload to YouTube (private by default — see below) |
| `doctor` | Environment check |
| `schedule --at 03:30` | Write a launchd plist for unattended nightly renders |

Runs are resumable. Every stage caches to disk, so a crash, a rate limit, or
Ctrl-C costs you only the unfinished stage:

```bash
python main.py run --resume 20260802-1844-norways-giant-mirrors
```

## Cost and speed

Roughly **$1.50–2.50** and **20–35 minutes** for an 8-minute video, dominated by
image generation (one `gpt-image-1` render per ~14s of narration) and the x264
encode. The exact estimate for each render is written to `manifest.json`.

To iterate on pacing and captions for free, skip the paid stages:

```bash
python main.py run --visuals gradient --seconds 90   # no image spend
```
and set `captions.align: estimate` in `config.yaml` to skip Whisper.

## Configuration

`config.yaml` holds the channel identity and every render knob; the flags above
override it per-run. The parts worth knowing:

- **`script.provider`** — `openai` (default, `gpt-4o`) or `anthropic`
  (`claude-opus-5`). Narration, imagery and caption alignment always use OpenAI.
- **`visuals.source`** — `ai` (gpt-image-1), `pexels` (stock, needs a key), or
  `gradient` (free procedural cards). A failure on any one scene degrades to a
  gradient rather than failing the render.
- **`video.transition`** — `xfade` for crossfades, `cut` for hard cuts. Cuts are
  substantially faster; crossfades re-encode the overlap.
- **`captions.renderer`** — `auto` picks `ass` when your ffmpeg has libass, and
  otherwise `overlay`, which draws cues with Pillow and composites them. The
  Homebrew ffmpeg bottle ships **without** libass, so `overlay` is what runs
  here. Both produce the same karaoke-highlighted style.
- **`audio.normalize`** — masters to −14 LUFS / −1.5 dBTP. YouTube turns loud
  audio down but never turns quiet audio up, so leaving this off makes your
  videos noticeably quieter than everything around them.

Drop `.mp3`s into `assets/music/` for a ducked music bed (see the README there
for sources cleared for monetisation). With the folder empty, videos render
narration-only.

## Uploading

Upload is deliberately **not** part of producing a video — nothing reaches
YouTube unless you ask for it, from the Library tab or the CLI:

```bash
python main.py upload 20260802-1844-norways-giant-mirrors                    # private
python main.py upload 20260802-1844-norways-giant-mirrors --privacy public   # needs the flag below
```

Publishing publicly needs **two** keys turned at once: `youtube.enabled: true`
in `config.yaml` *and* `--privacy public` on the command line. That way a
scheduled unattended run can never publish to your channel by accident.

One-time OAuth setup: Google Cloud Console → enable *YouTube Data API v3* →
Credentials → OAuth client ID → **Desktop app** → save the JSON to
`.secrets/client_secret.json`. The first upload opens a browser once and caches
a token.

## Scheduling

```bash
python main.py schedule --at 03:30 --count 1
```

writes a launchd plist and prints the two commands to install it. The scheduled
job renders only — uploads stay manual.

## The Scanner

What is pulling views on YouTube right now, and which topic veins sit underneath
it. Available as the **Scanner** tab or `main.py scan`.

```bash
python main.py scan --region US --category 27 --queue
```

It reads YouTube's official trending chart (`videos.list(chart="mostPopular")`).
That is public data, so it needs a plain **`YOUTUBE_API_KEY`** — *not* the OAuth
client used for uploading; the two are separate credentials. Create one by
enabling [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
→ Credentials → API key. A full scan costs about 4 of the 10,000 free daily
quota units and is cached for 6 hours, so browsing is free.

**Raw views are the least useful column.** The derived metrics are what "right
now" actually means:

| Metric | Why it's there |
|---|---|
| **Views/h** | Velocity. A 400K-view video 2 hours old is climbing faster than a 9M-view video from four days ago — and the table sorts on this by default. |
| **Engage** | (likes + comments) / views. Separates something people *react to* from something merely served to them. |
| **V/sub** | Views per subscriber. Did the topic travel beyond the channel's own base, or is it just a big channel posting? |
| **Age** | Separates a spike from a slow burn. |
| **Format** | Duration bucket — which length is winning in this category right now. |

Click any column to re-sort (numerically, not alphabetically), or double-click a
row to open the video.

**Topic veins** are the point of the tab. The chart returns individual videos, so
the configured LLM groups them into themes with aggregate view totals, a note on
why each is travelling, and a suggested topic in that vein that fits *your*
niche. **Send suggestion to queue** drops it straight into `topics.txt`, ready
for the Produce tab.

Scans are cached to `output/trends/<region>-<date>.json`. The tab opens showing
the most recent one, and keeping the files means week-over-week deltas are
possible later.

**Two limits worth keeping in view.** This is trending **videos in a region**,
not search demand — Google Trends has no official API and `pytrends` is
unofficial and rate-limited, so treat the chart as a proxy. And chasing whatever
spikes today is precisely the mass-produced-repetition pattern YouTube
demonetises (see below). The value here is spotting durable veins inside your
own niche, which is why the clustering prompt is told to prefer durable interest
over passing news and to flag clusters that don't suit the channel.

## Roadmap

- **Week-over-week deltas** — two or more cached scans are enough to show which
  veins are growing rather than merely large; the data is already on disk.
- `visuals.source: pexels` is implemented but has never been run — there was no
  `PEXELS_API_KEY` available when it was built.
- `video.transition: cut` is implemented and compiles into the filter graph, but
  only the `xfade` path has been exercised end to end.

## Before you point this at a real channel

Two things worth knowing, because they determine whether any of this earns
anything:

1. **Monetisation has a floor.** The YouTube Partner Programme needs 1,000
   subscribers plus 4,000 valid public watch hours (or 10M Shorts views) in 12
   months. Nothing here shortcuts that.
2. **Volume alone is a losing strategy.** YouTube's inauthentic-content policy
   targets mass-produced, repetitive material, and channels that publish
   undifferentiated AI output at scale get demonetised rather than rewarded.
   This tool is worth most as a production pipeline for a channel with a real
   editorial angle — pick topics you can stand behind, read the scripts before
   they go out, and check the facts. The script prompt refuses invented
   statistics and URLs, but an LLM writing narration unsupervised will still get
   things wrong, and the images are illustrations, not documentary footage.

## Layout

```
app.py              PySide6 desktop app
main.py             CLI entry point
build_app.sh        PyInstaller -> /Applications/vidforge.app
make_icon.py        draws assets/icon.icns

vidforge/
├── config.py       paths, .env, config.yaml (bundle-aware when frozen)
├── progress.py     stage/cancellation protocol shared by CLI and GUI
├── ffmpeg_utils.py binary discovery, probe, concat, filter detection
├── llm.py          provider-agnostic JSON completion (openai | anthropic)
├── ideation.py     topic queue + LLM idea generation
├── script.py       topic -> scenes, metadata, cost estimate
├── voice.py        per-scene TTS -> single narration track + timings
├── captions.py     word timing (whisper|estimate) -> ASS or PNG overlay
├── courses.py      PowerPoint course generation (topic or outline -> .pptx)
├── visuals.py      per-scene imagery, with graceful degradation
├── motion.py       Ken Burns clips (supersampled to kill zoompan jitter)
├── assemble.py     xfade chain, caption burn, music duck, loudness master
├── thumbnail.py    background + outlined text
├── trends.py       YouTube trending chart, metrics, topic clustering
├── metadata.py     title/description/tags/chapters
├── youtube.py      gated OAuth upload
├── pipeline.py     stage orchestration + resume
└── cli.py          argparse entry points
```
