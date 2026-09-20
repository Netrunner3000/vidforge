# vidforge — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)

---

## v2 — current

- [x] `P2` `feature` `@ai` Switched the default `visuals.image_model` from `gpt-image-1` to `gpt-image-2.5-flare` (config.yaml default + visuals.py fallback), matching what ships now.

- [ ] `P2` `docs` `@ai` **This pipeline has a second consumer now.** `imprint` imports
  `vidforge` as its Video mode rather than vendoring it, so `pipeline.produce()`,
  `progress.Reporter`/`STAGES` and the `video.*` / `script.*` config keys are a public API
  across repository boundaries — a rename here breaks a tab there, silently. Worth a
  compatibility note in any PR that touches those, and eventually a test on Imprint's side
  that fails loudly when a stage key disappears.

- [x] `P1` `bug` `@ai` **history.json is an unlocked read-modify-write shared by two apps.** Fixed same day: save() writes a temp file and `os.replace`s it; record() holds an `flock` across load-mutate-save; a corrupt index is quarantined as `history.corrupt-<stamp>.json` instead of being silently replaced by `[]` on the next save. Smoke-verified: interleaved records merge by slug, corrupt file quarantines. Original: load() mapped a decode error to `[]`, save() truncate-wrote, and Imprint + vidforge + the nightly launchd job interleaved record() calls.
- [ ] `P1` `bug` `@ai` **Size-only cache checks let truncated files poison resume.** voice.py:82 (mp3 > 1024), motion.py:131 (mp4 > 4096) and visuals.py:166 (png > 1024) are the sole validation on re-entry; a generation killed mid-write leaves a large-enough partial that ships into the final render. Write scene outputs to a temp name and rename on completion so a partial file never sits at the final path.
- [ ] `P2` `bug` `@ai` **Resume ignores config drift** — a 9:16 clip resumed under 16:9 defaults mixes cached geometry (pipeline.py:80). Stamp width/height/image_size into the scene manifest and invalidate cached scenes on mismatch.
- [ ] `P2` `design` `@ai` **Stop is a no-op through the longest stages and quitting orphans ffmpeg.** The cooperative cancel flag is only checked at reporter checkpoints (progress.py:60), and app exit leaves an in-flight ffmpeg child running (app.py:259 stop / app.py closeEvent). Check the flag between scenes at least, and terminate the ffmpeg child on quit.
- [x] `P2` `bug` `@ai` Caption ffconcat entries do not escape quotes, unlike `concat_demux` (captions.py:301). Fixed same day with the same `'\\''` escaping.
- [x] `P3` `security` `@ai` Trend scanner can leak the YouTube API key through requests exception text (trends.py:216). Fixed same day: `_get` wraps the request and re-raises a `TrendsError` with the key redacted — a `requests` connection error embeds the full URL, key included.
- [ ] `P2` `docs` `@ai` Image cost rate drift: script.py:163 manifests $0.04/image while Imprint reserves $0.06 and the docstring claims they match — pick one source of truth.
- [ ] `P2` `feature` `@ai` Migrate the script model default off `gpt-4o` (legacy, mid-purge at OpenAI) to `gpt-5.6-terra` (better and cheaper: $2/$12 vs $2.50/$10 per 1M) or `gpt-5.6-luna` for volume; keep `anthropic_model` current.
- [ ] `P1` `feature` `@me` **Get a `PEXELS_API_KEY`.** `visuals.source: pexels` is implemented but has never been run — there was no key available when it was built.
- [ ] `P1` `testing` `@ai` Exercise `video.transition: cut` end to end. It compiles into the filter graph but only the `xfade` path has been run.
- [ ] `P1` `feature` `@ai` Week-over-week trend deltas — two or more cached scans are enough to show which veins are growing rather than merely large, and the data is already on disk
- [ ] `P2` `bug` `@ai` Verify captions `whisper` vs `estimate` timing drift on a long script; the estimate path has only been checked on short ones
- [ ] `P2` `docs` `@ai` Document the cost estimate the script stage produces, alongside the actual spend after a run
- [ ] `P3` `performance` `@ai` Cache TTS per scene so a re-render after a visuals-only change doesn't re-bill the whole narration

## v3 — before pointing this at a real channel

- [ ] `P0` `research` `@me` **Editorial angle first.** YouTube's inauthentic-content policy targets mass-produced, repetitive material; undifferentiated AI output at scale gets demonetised rather than rewarded. This is a production pipeline for a channel with a real angle, not a volume play.
- [ ] `P0` `research` `@me` Read every script and check the facts before publishing. The prompt refuses invented statistics and URLs, but an unsupervised LLM will still get things wrong, and the images are illustrations rather than footage.
- [ ] `P1` `research` `@me` Monetisation floor: 1,000 subscribers plus 4,000 valid public watch hours (or 10M Shorts views) in 12 months. Nothing here shortcuts that.
- [ ] `P2` `feature` `@ai` A review gate in the GUI — script and thumbnail approved by a human before assembly starts
- [ ] `P3` `feature` `@ai` Per-video post-mortem: predicted cost vs actual, plus retention once the video has data
