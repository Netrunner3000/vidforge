"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import courses, fonts, history, ideation, llm, pipeline
from .config import MUSIC_DIR, PROJECT_ROOT, Config, optional_key, output_root
from .ffmpeg_utils import FFmpegError, ffmpeg_bin, ffprobe_bin, has_filter
from .progress import ConsoleReporter

LAUNCHD_LABEL = "com.andreas.vidforge"


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.load()
    cfg.apply_overrides(
        {
            "script.provider": args.provider,
            "script.target_seconds": args.seconds,
            "visuals.source": args.visuals,
            "voice.voice": args.voice,
            "video.transition": "cut" if args.no_transitions else None,
            "captions.enabled": False if args.no_captions else None,
            "music.enabled": False if args.no_music else None,
        }
    )

    ok, message = llm.provider_available(cfg)
    if not ok:
        print(f"error: {message}", file=sys.stderr)
        return 1

    if args.resume:
        pipeline.produce(cfg, resume_slug=args.resume)
        return 0

    failures = 0
    for i in range(args.count):
        if args.count > 1:
            print(f"\n=== video {i + 1}/{args.count} ===")
        try:
            # An explicit --topic only applies to the first video; the rest come
            # from the queue, otherwise a batch would make the same video twice.
            pipeline.produce(cfg, topic=args.topic if i == 0 else None)
        except llm.LLMRefusal as exc:
            print(f"error: {exc}", file=sys.stderr)
            failures += 1
        except Exception as exc:  # noqa: BLE001 - one bad video shouldn't stop a batch
            print(f"error: video {i + 1} failed: {exc}", file=sys.stderr)
            failures += 1
            if args.count == 1:
                return 1

    return 1 if failures == args.count else 0


# --------------------------------------------------------------------------
# topics
# --------------------------------------------------------------------------


def cmd_topics(args: argparse.Namespace) -> int:
    cfg = Config.load()

    if args.add:
        added = ideation.append_to_queue(args.add)
        print(f"added {added} topic(s) to topics.txt")
        return 0

    if args.suggest:
        ideas = ideation.suggest(cfg, args.suggest)
        if not ideas:
            print("no ideas returned")
            return 1
        for idea in ideas:
            print(f"  {idea['topic']}")
            print(f"      {idea.get('why_it_works', '')}")
        added = ideation.append_to_queue([i["topic"] for i in ideas])
        print(f"\nappended {added} new topic(s) to topics.txt")
        return 0

    queue = ideation.read_queue()
    print(f"{len(queue)} topic(s) queued:\n")
    for topic in queue:
        marker = "done" if not history.is_new(topic) else "    "
        print(f"  [{marker}] {topic}")
    print(f"\n{len(history.load())} video(s) produced so far")
    return 0


# --------------------------------------------------------------------------
# list / upload
# --------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    entries = history.load()
    if not entries:
        print("nothing produced yet — try `main.py run`")
        return 0

    for entry in entries[-args.limit :]:
        state = "published" if entry.get("published") else "local"
        length = entry.get("duration_seconds", 0) / 60
        print(f"  {entry['slug']}")
        print(f"      {entry.get('title', '')}")
        print(f"      {length:.1f} min · {state}")
        if entry.get("url"):
            print(f"      {entry['url']}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    from . import youtube

    cfg = Config.load()
    root = output_root() / args.slug
    video = root / f"{args.slug}.mp4"
    meta_path = root / "metadata.json"

    if not video.exists():
        print(f"error: no video at {video}", file=sys.stderr)
        return 1
    if not meta_path.exists():
        print(f"error: no metadata.json at {meta_path}", file=sys.stderr)
        return 1

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    thumb = root / "thumbnail.jpg"

    print(f"→ uploading {args.slug} as {args.privacy}")
    print(f"  title: {meta['title']}")

    try:
        result = youtube.upload(
            cfg,
            video,
            meta,
            privacy=args.privacy,
            thumbnail=thumb if thumb.exists() else None,
        )
    except youtube.YouTubeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    history.record(
        {
            "slug": args.slug,
            "published": result["privacy"] == "public",
            "privacy": result["privacy"],
            "video_id": result["video_id"],
            "url": result["url"],
            "uploaded": history.utcnow(),
        }
    )
    return 0


# --------------------------------------------------------------------------
# course
# --------------------------------------------------------------------------


def cmd_course(args: argparse.Namespace) -> int:
    """Generate a PowerPoint course presentation from a topic or outline."""
    cfg = Config.load()
    reporter = ConsoleReporter()

    ok, message = llm.provider_available(cfg)
    if not ok:
        print(f"error: {message}", file=sys.stderr)
        return 1

    gen = courses.CourseGenerator(output_root=output_root())

    try:
        if args.outline:
            # Load outline from file
            outline_path = Path(args.outline)
            if not outline_path.exists():
                print(f"error: outline file not found: {args.outline}", file=sys.stderr)
                return 1
            with open(outline_path) as f:
                outline = f.read()
            output_path = gen.create_presentation(
                topic=args.topic or outline_path.stem,
                outline=outline,
                output_file=args.output,
                progress=reporter
            )
        else:
            # Generate outline from topic
            if not args.topic:
                print("error: either --topic or --outline must be provided", file=sys.stderr)
                return 1
            output_path = gen.create_presentation(
                topic=args.topic,
                output_file=args.output,
                progress=reporter
            )

        print(f"✅ Course generated: {output_path}")
        if args.open:
            import subprocess
            subprocess.run(["open", str(output_path)])
        return 0

    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# doctor / scan / schedule
# --------------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    from . import trends

    cfg = Config.load()
    try:
        result = trends.scan(
            cfg,
            region=args.region,
            category_id=args.category,
            limit=args.limit,
            cluster=not args.no_cluster,
            refresh=args.refresh,
            reporter=ConsoleReporter(),
        )
    except trends.MissingKey as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    except trends.TrendsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print(f"\nTrending in {result.region} — {result.fetched_at}")
    print(f"{len(result.videos)} videos · {result.quota_units} quota units\n")

    header = (
        f"{'#':>3}  {'views':>7} {'views/h':>8} {'engage':>7} {'v/sub':>6} "
        f"{'age':>6}  {'format':<15} {'category':<18} title"
    )
    print(header)
    print("-" * len(header))
    for i, video in enumerate(result.videos[: args.top], 1):
        age = f"{video.age_hours:.0f}h" if video.age_hours < 72 else f"{video.age_hours / 24:.0f}d"
        vps = f"{video.views_per_subscriber:.2f}" if video.subscribers else "—"
        print(
            f"{i:>3}  {trends.compact(video.views):>7} "
            f"{trends.compact(video.views_per_hour):>8} "
            f"{video.engagement_rate * 100:>6.1f}% {vps:>6} {age:>6}  "
            f"{video.bucket:<15} {video.category[:18]:<18} {video.title[:58]}"
        )

    if result.clusters:
        print("\nTopic veins (highest velocity first)\n")
        for cluster in result.clusters:
            print(
                f"  {cluster.label}  —  {cluster.videos} videos · "
                f"{trends.compact(cluster.total_views)} views · "
                f"{trends.compact(cluster.avg_views_per_hour)}/h avg"
            )
            print(f"      {cluster.why_it_travels}")
            print(f"      → suggested: {cluster.suggested_topic}")
        if args.queue:
            added = ideation.append_to_queue(
                [c.suggested_topic for c in result.clusters if c.suggested_topic]
            )
            print(f"\nadded {added} suggested topic(s) to topics.txt")
        else:
            print("\n(re-run with --queue to add the suggested topics to topics.txt)")

    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print("vidforge environment check\n")
    problems = 0

    try:
        print(f"  ffmpeg    {ffmpeg_bin()}")
        print(f"  ffprobe   {ffprobe_bin()}")
        for name, required in (
            ("zoompan", True),
            ("xfade", False),
            ("overlay", True),
            ("concat", True),
            ("subtitles", False),
            ("sidechaincompress", False),
        ):
            present = has_filter(name)
            note = "" if present else ("  <- REQUIRED" if required else "  (optional)")
            print(f"    filter {name:<20} {'ok' if present else 'missing'}{note}")
            if required and not present:
                problems += 1
        if not has_filter("subtitles"):
            print("      no libass -> captions use the Pillow overlay renderer")
        if not has_filter("xfade"):
            print("      no xfade -> set video.transition: cut")
    except FFmpegError as exc:
        print(f"  ffmpeg    MISSING — {exc}")
        problems += 1

    print()
    print(f"  fonts     {'found' if fonts.available() else 'NONE — captions/thumbnails need a TTF'}")
    if not fonts.available():
        problems += 1

    print()
    cfg = Config.load()
    ok, message = llm.provider_available(cfg)
    print(f"  script    {message}")
    problems += 0 if ok else 1

    for key, why in (
        ("OPENAI_API_KEY", "narration, images, caption alignment"),
        ("ANTHROPIC_API_KEY", "optional Anthropic script backend"),
        ("PEXELS_API_KEY", "optional stock-photo visuals"),
        ("YOUTUBE_API_KEY", "optional trend scanner (`main.py scan`)"),
    ):
        state = "set" if optional_key(key) else "not set"
        print(f"  {key:<20} {state:<8} ({why})")
    if not optional_key("OPENAI_API_KEY"):
        problems += 1

    print()
    for module, why in (
        ("PIL", "thumbnails"),
        ("yaml", "config"),
        ("pptx", "course generation"),
        ("googleapiclient", "YouTube upload (optional)"),
    ):
        try:
            __import__(module)
            print(f"  python    {module:<18} ok  ({why})")
        except ImportError:
            print(f"  python    {module:<18} missing ({why})")
            if module != "googleapiclient":
                problems += 1

    tracks = (
        [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav")]
        if MUSIC_DIR.exists()
        else []
    )
    print(f"\n  music     {len(tracks)} track(s) in assets/music")
    print(f"  output    {output_root()}")

    print("\n" + ("all good" if problems == 0 else f"{problems} problem(s) to fix"))
    return 0 if problems == 0 else 1


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{main}</string>
    <string>run</string>
    <string>--count</string>
    <string>{count}</string>
  </array>
  <key>WorkingDirectory</key><string>{cwd}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key><string>{logs}/vidforge.log</string>
  <key>StandardErrorPath</key><string>{logs}/vidforge.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
"""


def cmd_schedule(args: argparse.Namespace) -> int:
    logs = PROJECT_ROOT / "logs"
    logs.mkdir(exist_ok=True)

    hour, _, minute = args.at.partition(":")
    plist = PLIST.format(
        label=LAUNCHD_LABEL,
        python=sys.executable,
        main=PROJECT_ROOT / "main.py",
        cwd=PROJECT_ROOT,
        logs=logs,
        count=args.count,
        hour=int(hour),
        minute=int(minute or 0),
    )

    dst = PROJECT_ROOT / f"{LAUNCHD_LABEL}.plist"
    dst.write_text(plist, encoding="utf-8")
    target = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

    print(f"wrote {dst}\n")
    print("To install it (renders but never uploads — upload stays manual):\n")
    print(f"  cp {dst} {target}")
    print(f"  launchctl unload {target} 2>/dev/null; launchctl load {target}\n")
    print(f"Logs will land in {logs}/vidforge.log")
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vidforge",
        description="Autonomously produce narrated, illustrated YouTube videos.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="produce one or more complete videos")
    run.add_argument("--topic", help="explicit topic; otherwise pulled from topics.txt")
    run.add_argument("--count", type=int, default=1, help="how many videos to produce")
    run.add_argument("--resume", help="resume a partially-built slug")
    run.add_argument("--provider", choices=["openai", "anthropic"], help="script backend")
    run.add_argument("--seconds", type=int, help="target video length in seconds")
    run.add_argument("--visuals", choices=["ai", "pexels", "gradient"], help="image source")
    run.add_argument("--voice", help="TTS voice name")
    run.add_argument("--no-captions", action="store_true")
    run.add_argument("--no-music", action="store_true")
    run.add_argument("--no-transitions", action="store_true", help="hard cuts (much faster)")
    run.set_defaults(func=cmd_run)

    topics = sub.add_parser("topics", help="inspect or extend the topic queue")
    topics.add_argument("--add", nargs="+", metavar="TOPIC")
    topics.add_argument("--suggest", type=int, metavar="N", help="generate N new ideas")
    topics.set_defaults(func=cmd_topics)

    listing = sub.add_parser("list", help="list produced videos")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=cmd_list)

    upload = sub.add_parser("upload", help="upload a rendered video to YouTube")
    upload.add_argument("slug")
    upload.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        default="private",
        help="defaults to private; 'public' also needs youtube.enabled in config.yaml",
    )
    upload.set_defaults(func=cmd_upload)

    scan = sub.add_parser("scan", help="what is pulling views on YouTube right now")
    scan.add_argument("--region", help="ISO country code (default from config.yaml)")
    scan.add_argument("--category", help="YouTube category id, e.g. 27=Education")
    scan.add_argument("--limit", type=int, help="videos to fetch (max 200)")
    scan.add_argument("--top", type=int, default=25, help="rows to print")
    scan.add_argument("--no-cluster", action="store_true", help="skip LLM topic grouping")
    scan.add_argument("--queue", action="store_true", help="append suggested topics")
    scan.add_argument("--refresh", action="store_true", help="ignore the cached scan")
    scan.add_argument("--json", action="store_true", help="raw JSON instead of a table")
    scan.set_defaults(func=cmd_scan)

    course = sub.add_parser("course", help="generate a PowerPoint course presentation")
    course.add_argument("--topic", help="course topic for outline generation")
    course.add_argument("--outline", metavar="FILE", help="path to pre-structured course outline file")
    course.add_argument("--output", metavar="FILENAME", help="output .pptx filename (default: auto-generated)")
    course.add_argument("--open", action="store_true", help="open the file in your default viewer")
    course.set_defaults(func=cmd_course)

    doctor = sub.add_parser("doctor", help="check tools, keys and dependencies")
    doctor.set_defaults(func=cmd_doctor)

    schedule = sub.add_parser("schedule", help="write a launchd plist for nightly runs")
    schedule.add_argument("--at", default="03:00", metavar="HH:MM")
    schedule.add_argument("--count", type=int, default=1)
    schedule.set_defaults(func=cmd_schedule)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted — re-run with `--resume <slug>` to pick up where it stopped")
        return 130
