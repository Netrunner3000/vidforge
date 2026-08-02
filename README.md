# vidforge

Autonomously turns a topic into a finished, narrated, illustrated YouTube video:
script → voiceover → per-scene imagery → Ken Burns motion → burned-in captions →
music bed → thumbnail → metadata. One command per video.

```bash
python main.py run --topic "How undersea cables carry the entire internet"
```

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

## Commands

| Command | What it does |
|---|---|
| `run` | Produce a video. `--topic`, `--count N`, `--resume SLUG`, `--seconds`, `--visuals`, `--provider` |
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

Upload is deliberately **not** part of `run` — nothing reaches YouTube unless you
invoke it yourself:

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
vidforge/
├── config.py       paths, .env, config.yaml
├── ffmpeg_utils.py binary discovery, probe, concat, filter detection
├── llm.py          provider-agnostic JSON completion (openai | anthropic)
├── ideation.py     topic queue + LLM idea generation
├── script.py       topic -> scenes, metadata, cost estimate
├── voice.py        per-scene TTS -> single narration track + timings
├── captions.py     word timing (whisper|estimate) -> ASS or PNG overlay
├── visuals.py      per-scene imagery, with graceful degradation
├── motion.py       Ken Burns clips (supersampled to kill zoompan jitter)
├── assemble.py     xfade chain, caption burn, music duck, loudness master
├── thumbnail.py    background + outlined text
├── metadata.py     title/description/tags/chapters
├── youtube.py      gated OAuth upload
├── pipeline.py     stage orchestration + resume
└── cli.py          argparse entry points
```
