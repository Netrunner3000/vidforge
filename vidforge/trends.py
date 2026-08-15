"""What is pulling views on YouTube right now.

Reads the official trending chart (YouTube Data API v3, `videos.list` with
`chart=mostPopular`), computes the derived metrics that actually carry signal,
and optionally clusters the videos into topics with the configured LLM.

Quota: `videos.list`, `videoCategories.list` and `channels.list` cost 1 unit per
call against a default 10,000 units/day, so a full scan is 3-6 units. Scans are
cached so repeat opens cost nothing.

This endpoint reads public data and needs a plain API key — NOT the OAuth client
used for uploading. The two are separate credentials.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import Config, optional_key, output_root
from .llm import complete_json
from .progress import Reporter

API_ROOT = "https://www.googleapis.com/youtube/v3"
MAX_PER_PAGE = 50
MAX_CHART_RESULTS = 200  # the mostPopular chart will not paginate past this
TIMEOUT = 30

CONSOLE_URL = "https://console.cloud.google.com/apis/library/youtube.googleapis.com"


class TrendsError(RuntimeError):
    pass


class MissingKey(TrendsError):
    pass


class QuotaExceeded(TrendsError):
    pass


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_duration(value: str) -> int:
    """ISO-8601 duration (PT4M13S, P1DT2H3M4S) to seconds. 0 if unparseable."""
    match = _DURATION.match(value or "")
    if not match:
        return 0
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def parse_timestamp(value: str) -> datetime:
    # RFC-3339 with a trailing Z, which fromisoformat rejects before 3.11.
    return datetime.fromisoformat((value or "").replace("Z", "+00:00"))


def duration_bucket(seconds: int) -> str:
    if seconds <= 0:
        return "live/unknown"
    if seconds < 60:
        return "short (<1m)"
    if seconds < 8 * 60:
        return "mid (1-8m)"
    if seconds < 20 * 60:
        return "long (8-20m)"
    return "extended (20m+)"


def compact(number: float) -> str:
    """1234567 -> '1.2M'. Used by the CLI table and the app."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= limit:
            return f"{number / limit:.1f}{suffix}"
    return f"{number:.0f}"


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


@dataclass
class TrendingVideo:
    video_id: str
    title: str
    channel: str
    channel_id: str
    category_id: str
    category: str
    published_at: str
    views: int
    likes: int
    comments: int
    duration_seconds: int
    tags: list[str]
    is_live: bool

    # derived
    age_hours: float = 0.0
    views_per_hour: float = 0.0
    engagement_rate: float = 0.0
    subscribers: int = 0
    views_per_subscriber: float = 0.0

    @property
    def url(self) -> str:
        return f"https://youtu.be/{self.video_id}"

    @property
    def bucket(self) -> str:
        return duration_bucket(self.duration_seconds)

    def compute(self, now: datetime | None = None) -> "TrendingVideo":
        now = now or datetime.now(timezone.utc)
        try:
            age = (now - parse_timestamp(self.published_at)).total_seconds() / 3600
        except ValueError:
            age = 0.0
        # Floor at one hour: a video minutes old would otherwise show an
        # enormous views/hour purely from dividing by a tiny denominator.
        self.age_hours = max(age, 0.0)
        self.views_per_hour = self.views / max(self.age_hours, 1.0)
        self.engagement_rate = (
            (self.likes + self.comments) / self.views if self.views else 0.0
        )
        self.views_per_subscriber = (
            self.views / self.subscribers if self.subscribers else 0.0
        )
        return self


@dataclass
class TopicCluster:
    label: str
    why_it_travels: str
    suggested_topic: str
    video_ids: list[str] = field(default_factory=list)
    total_views: int = 0
    videos: int = 0
    avg_views_per_hour: float = 0.0
    avg_engagement: float = 0.0


@dataclass
class Scan:
    region: str
    fetched_at: str
    quota_units: int
    videos: list[TrendingVideo] = field(default_factory=list)
    clusters: list[TopicCluster] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "fetched_at": self.fetched_at,
            "quota_units": self.quota_units,
            "videos": [asdict(v) for v in self.videos],
            "clusters": [asdict(c) for c in self.clusters],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scan":
        return cls(
            region=data["region"],
            fetched_at=data["fetched_at"],
            quota_units=data.get("quota_units", 0),
            videos=[TrendingVideo(**v) for v in data.get("videos", [])],
            clusters=[TopicCluster(**c) for c in data.get("clusters", [])],
        )


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def api_key() -> str:
    """YOUTUBE_API_KEY, or GOOGLE_API_KEY as a fallback."""
    key = optional_key("YOUTUBE_API_KEY") or optional_key("GOOGLE_API_KEY")
    if not key:
        raise MissingKey(
            "No YouTube API key. The scanner reads public trending data, which "
            "needs a plain API key — not the OAuth client used for uploading.\n\n"
            f"  1. Enable the YouTube Data API v3:\n     {CONSOLE_URL}\n"
            "  2. Credentials -> Create credentials -> API key\n"
            "  3. Put it in your .env as:  YOUTUBE_API_KEY=..."
        )
    return key


def _get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    params = {**params, "key": api_key()}
    response = requests.get(f"{API_ROOT}/{endpoint}", params=params, timeout=TIMEOUT)

    if response.status_code == 403:
        reasons = {
            e.get("reason") for e in response.json().get("error", {}).get("errors", [])
        }
        if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
            raise QuotaExceeded(
                "YouTube API daily quota exhausted (10,000 units by default). "
                "It resets at midnight Pacific. Cached scans still open."
            )
        raise TrendsError(
            "YouTube API refused the key (403). Check that the YouTube Data API "
            f"v3 is enabled for this key's project:\n{CONSOLE_URL}"
        )
    if response.status_code == 401:
        raise TrendsError(
            "YouTube API rejected the credential (401). This endpoint needs a "
            "Google Cloud *API key*; an OAuth token or an AI-Studio key will not "
            "work here."
        )
    if not response.ok:
        message = response.json().get("error", {}).get("message", response.text[:200])
        raise TrendsError(f"YouTube API error {response.status_code}: {message}")

    return response.json()


def fetch_categories(region: str) -> dict[str, str]:
    """Category id -> name. 1 quota unit."""
    try:
        data = _get("videoCategories", {"part": "snippet", "regionCode": region})
    except TrendsError:
        return {}
    return {item["id"]: item["snippet"]["title"] for item in data.get("items", [])}


def fetch_trending(
    region: str = "US",
    category_id: str | None = None,
    limit: int = 50,
    reporter: Reporter | None = None,
) -> tuple[list[TrendingVideo], int]:
    """Pull the mostPopular chart. Returns (videos, quota units spent)."""
    reporter = reporter or Reporter()
    limit = max(1, min(limit, MAX_CHART_RESULTS))
    categories = fetch_categories(region)
    units = 1

    videos: list[TrendingVideo] = []
    page_token: str | None = None

    while len(videos) < limit:
        reporter.raise_if_cancelled()
        params: dict[str, Any] = {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region,
            "maxResults": min(MAX_PER_PAGE, limit - len(videos)),
        }
        if category_id:
            params["videoCategoryId"] = str(category_id)
        if page_token:
            params["pageToken"] = page_token

        data = _get("videos", params)
        units += 1

        for item in data.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})
            cat_id = snippet.get("categoryId", "")
            videos.append(
                TrendingVideo(
                    video_id=item["id"],
                    title=snippet.get("title", ""),
                    channel=snippet.get("channelTitle", ""),
                    channel_id=snippet.get("channelId", ""),
                    category_id=cat_id,
                    category=categories.get(cat_id, cat_id),
                    published_at=snippet.get("publishedAt", ""),
                    views=int(stats.get("viewCount", 0) or 0),
                    likes=int(stats.get("likeCount", 0) or 0),
                    comments=int(stats.get("commentCount", 0) or 0),
                    duration_seconds=parse_duration(details.get("duration", "")),
                    tags=snippet.get("tags", []) or [],
                    is_live=snippet.get("liveBroadcastContent", "none") != "none",
                )
            )

        reporter.substep(len(videos), limit, f"{len(videos)} videos")
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return videos[:limit], units


def attach_subscribers(videos: list[TrendingVideo]) -> int:
    """Fill in subscriber counts so views-per-subscriber means something.

    1 quota unit per 50 channels. Failure is non-fatal — the metric is simply
    left at zero rather than losing the whole scan.
    """
    ids = sorted({v.channel_id for v in videos if v.channel_id})
    units = 0
    subscribers: dict[str, int] = {}

    for start in range(0, len(ids), MAX_PER_PAGE):
        chunk = ids[start : start + MAX_PER_PAGE]
        try:
            data = _get("channels", {"part": "statistics", "id": ",".join(chunk)})
            units += 1
        except TrendsError:
            break
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            if not stats.get("hiddenSubscriberCount"):
                subscribers[item["id"]] = int(stats.get("subscriberCount", 0) or 0)

    for video in videos:
        video.subscribers = subscribers.get(video.channel_id, 0)
    return units


# --------------------------------------------------------------------------
# Topic clustering
# --------------------------------------------------------------------------

CLUSTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clusters"],
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "label",
                    "why_it_travels",
                    "suggested_topic",
                    "video_indexes",
                ],
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Short topic label, 2-6 words.",
                    },
                    "why_it_travels": {
                        "type": "string",
                        "description": "One sentence on why this is getting views now.",
                    },
                    "suggested_topic": {
                        "type": "string",
                        "description": (
                            "A specific video topic in this vein that suits the "
                            "channel's niche. Must be researchable from public "
                            "facts, not a reaction to a passing news event."
                        ),
                    },
                    "video_indexes": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Indexes of the listed videos in this cluster.",
                    },
                },
            },
        }
    },
}


def cluster_topics(
    cfg: Config, videos: list[TrendingVideo], max_clusters: int = 8
) -> list[TopicCluster]:
    """Group trending videos into topic veins via the configured LLM."""
    if not videos:
        return []

    listing = "\n".join(
        f"{i}. [{v.category}] {v.title} — {compact(v.views)} views, "
        f"{compact(v.views_per_hour)}/h, {v.bucket}"
        for i, v in enumerate(videos)
    )

    system = (
        "You analyse YouTube trending data for a documentary channel. You group "
        "videos into topic veins and judge which ones represent durable interest "
        "rather than a one-off news spike. You never invent videos or metrics."
    )
    user = (
        f"Channel niche: {cfg.get('channel.niche')}\n"
        f"Audience: {cfg.get('channel.audience')}\n\n"
        f"These videos are trending right now:\n\n{listing}\n\n"
        f"Group them into at most {max_clusters} topic clusters. For each, give a "
        "label, one sentence on why it is travelling, and one suggested video "
        "topic in that vein that fits the channel's niche.\n\n"
        "Prefer veins with durable interest over passing news, celebrity gossip, "
        "or anything that needs footage the channel cannot license. If a cluster "
        "does not suit the niche at all, still report it but say so in the reason."
    )

    data = complete_json(
        cfg, system=system, user=user, schema=CLUSTER_SCHEMA, name="trend_clusters"
    )

    clusters: list[TopicCluster] = []
    for raw in data.get("clusters", [])[:max_clusters]:
        members = [
            videos[i]
            for i in raw.get("video_indexes", [])
            if isinstance(i, int) and 0 <= i < len(videos)
        ]
        if not members:
            continue
        clusters.append(
            TopicCluster(
                label=raw.get("label", "").strip(),
                why_it_travels=raw.get("why_it_travels", "").strip(),
                suggested_topic=raw.get("suggested_topic", "").strip(),
                video_ids=[v.video_id for v in members],
                videos=len(members),
                total_views=sum(v.views for v in members),
                avg_views_per_hour=sum(v.views_per_hour for v in members) / len(members),
                avg_engagement=sum(v.engagement_rate for v in members) / len(members),
            )
        )

    clusters.sort(key=lambda c: c.avg_views_per_hour, reverse=True)
    return clusters


# --------------------------------------------------------------------------
# Cache + orchestration
# --------------------------------------------------------------------------


def cache_dir() -> Path:
    path = output_root() / "trends"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(region: str, category_id: str | None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = f"-cat{category_id}" if category_id else ""
    return cache_dir() / f"{region}{suffix}-{stamp}.json"


def load_cached(region: str, category_id: str | None, max_age_hours: float) -> Scan | None:
    path = cache_path(region, category_id)
    if not path.exists():
        return None
    try:
        scan = Scan.from_dict(json.loads(path.read_text(encoding="utf-8")))
        age = (
            datetime.now(timezone.utc) - parse_timestamp(scan.fetched_at)
        ).total_seconds() / 3600
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
    return scan if age <= max_age_hours else None


def history_files(region: str | None = None) -> list[Path]:
    pattern = f"{region}*.json" if region else "*.json"
    return sorted(cache_dir().glob(pattern))


def scan(
    cfg: Config,
    *,
    region: str | None = None,
    category_id: str | None = None,
    limit: int | None = None,
    cluster: bool | None = None,
    refresh: bool = False,
    reporter: Reporter | None = None,
) -> Scan:
    """Fetch (or reuse) a trending scan, with metrics and optional clustering."""
    reporter = reporter or Reporter()
    region = (region or cfg.get("trends.region", "US")).upper()
    limit = limit or int(cfg.get("trends.limit", 50))
    cluster = cfg.get("trends.cluster", True) if cluster is None else cluster
    max_age = float(cfg.get("trends.cache_hours", 6))

    if not refresh:
        cached = load_cached(region, category_id, max_age)
        if cached:
            reporter.log(f"using cached scan from {cached.fetched_at} (0 quota units)")
            return cached

    reporter.log(f"fetching trending chart for {region}")
    videos, units = fetch_trending(region, category_id, limit, reporter)
    if not videos:
        raise TrendsError(f"YouTube returned no trending videos for region {region!r}")

    units += attach_subscribers(videos)
    now = datetime.now(timezone.utc)
    for video in videos:
        video.compute(now)
    videos.sort(key=lambda v: v.views_per_hour, reverse=True)
    reporter.log(f"{len(videos)} videos · {units} quota units")

    clusters: list[TopicCluster] = []
    if cluster:
        reporter.log("clustering into topics")
        try:
            clusters = cluster_topics(
                cfg, videos, int(cfg.get("trends.max_clusters", 8))
            )
            reporter.log(f"{len(clusters)} topic clusters")
        except Exception as exc:  # noqa: BLE001 - clustering is a bonus, not the point
            reporter.log(f"! clustering failed ({exc}); showing videos only")

    result = Scan(
        region=region,
        fetched_at=now.isoformat(timespec="seconds"),
        quota_units=units,
        videos=videos,
        clusters=clusters,
    )
    cache_path(region, category_id).write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
