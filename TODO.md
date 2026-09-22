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
- [x] `P1` `bug` `@ai` **Size-only cache checks let truncated files poison resume.** Fixed 2026-09-21: voice, motion and visuals all write scene outputs to a `.tmp.<ext>` name and rename on completion, so a generation killed mid-write can never leave a large-enough partial at the cached path (the size checks stay as the fast validity probe).
- [x] `P2` `bug` `@ai` **Resume ignores config drift.** Fixed 2026-09-21: the build manifest stamps geometry (width/height/fps/image_size) at creation, and `_check_geometry()` on resume discards cached images and clips when it changed — narration stays cached, an mp3 has no geometry. Pre-stamp builds adopt the current geometry without wiping (their drift is unknowable).
- [x] `P2` `design` `@ai` **Stop is a no-op through the longest stages and quitting orphans ffmpeg.** Fixed 2026-09-21: `ffmpeg_utils.run` tracks its children and `terminate_active()` kills them — wired into `ProduceWorker.stop()` (which closeEvent already routes through), safe mid-write thanks to the temp+rename above; the worker maps a terminate-during-cancel to *cancelled*, not *failed*. Cancellation between steps was already there via the reporter checkpoints.
- [x] `P2` `bug` `@ai` Caption ffconcat entries do not escape quotes, unlike `concat_demux` (captions.py:301). Fixed same day with the same `'\\''` escaping.
- [x] `P3` `security` `@ai` Trend scanner can leak the YouTube API key through requests exception text (trends.py:216). Fixed same day: `_get` wraps the request and re-raises a `TrendsError` with the key redacted — a `requests` connection error embeds the full URL, key included.
- [x] `P2` `docs` `@ai` Image cost rate drift. Fixed 2026-09-21: `estimate_cost_usd` uses $0.06/image, matching Imprint's per-unit reserve, with a comment naming that as the source of truth — and its TTS line was also corrected from the input-token-only formula to ~$0.015/1k characters (audio output dominates; the old number understated narration ~100×), mirrored in Imprint's `pre_estimate`.
- [x] `P2` `feature` `@ai` Migrate the script model default off `gpt-4o`. Done 2026-09-21: `config.yaml` and llm.py's fallback default to `gpt-5.6-terra` ($2/$12 vs gpt-4o's $2.50/$10 per 1M, and current rather than mid-purge); `gpt-5.6-luna` noted as the volume option. Existing installs keep the model in their seeded Application Support config until they change it.
- [ ] `P1` `feature` `@me` **Get a `PEXELS_API_KEY`.** `visuals.source: pexels` is implemented but has never been run — there was no key available when it was built.
- [ ] `P1` `testing` `@ai` Exercise `video.transition: cut` end to end. It compiles into the filter graph but only the `xfade` path has been run.
- [ ] `P1` `feature` `@ai` Week-over-week trend deltas — two or more cached scans are enough to show which veins are growing rather than merely large, and the data is already on disk
- [ ] `P2` `bug` `@ai` Verify captions `whisper` vs `estimate` timing drift on a long script; the estimate path has only been checked on short ones
- [ ] `P2` `docs` `@ai` Document the cost estimate the script stage produces, alongside the actual spend after a run
- [ ] `P3` `performance` `@ai` Cache TTS per scene so a re-render after a visuals-only change doesn't re-bill the whole narration

- [x] `P2` `infra` `@ai` **Versioned `v<MAJOR>.<BUILD>`, shown in the window title.** The
  arc lives in `VERSION`; the build is `git rev-list --count HEAD`, so it cannot be
  forgotten. `vidforge/version.py` reads live git from a checkout and a `_build_info.json` stamped
  by `scripts/stamp_version.py` from a frozen bundle, and says `v2.???` rather than
  guessing when it has neither. This was the last packaged app in the lab still missing
  the scheme.

## v3 — before pointing this at a real channel

- [ ] `P0` `research` `@me` **Editorial angle first.** YouTube's inauthentic-content policy targets mass-produced, repetitive material; undifferentiated AI output at scale gets demonetised rather than rewarded. This is a production pipeline for a channel with a real angle, not a volume play.
- [ ] `P0` `research` `@me` Read every script and check the facts before publishing. The prompt refuses invented statistics and URLs, but an unsupervised LLM will still get things wrong, and the images are illustrations rather than footage.
- [ ] `P1` `research` `@me` Monetisation floor: 1,000 subscribers plus 4,000 valid public watch hours (or 10M Shorts views) in 12 months. Nothing here shortcuts that.
- [ ] `P2` `feature` `@ai` A review gate in the GUI — script and thumbnail approved by a human before assembly starts
- [ ] `P3` `feature` `@ai` Per-video post-mortem: predicted cost vs actual, plus retention once the video has data
