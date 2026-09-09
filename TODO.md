# vidforge — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)

---

## v2 — current

- [ ] `P2` `docs` `@ai` **This pipeline has a second consumer now.** `imprint` imports
  `vidforge` as its Video mode rather than vendoring it, so `pipeline.produce()`,
  `progress.Reporter`/`STAGES` and the `video.*` / `script.*` config keys are a public API
  across repository boundaries — a rename here breaks a tab there, silently. Worth a
  compatibility note in any PR that touches those, and eventually a test on Imprint's side
  that fails loudly when a stage key disappears.

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
