"""YouTube Data API v3 upload.

Deliberately gated. Nothing here runs as part of `main.py run` — you invoke
`main.py upload <slug>` yourself. Uploads default to `private`; publishing
publicly additionally requires `youtube.enabled: true` in config.yaml and an
explicit `--privacy public` on the command line.

Setup (one time):
  1. Google Cloud Console -> new project -> enable "YouTube Data API v3"
  2. Credentials -> Create credentials -> OAuth client ID -> Desktop app
  3. Download the JSON to .secrets/client_secret.json
     (or point VIDFORGE_YT_CLIENT_SECRET at it)
  4. `python main.py upload <slug>` opens a browser once and caches a token
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import SECRETS_DIR, Config, load_env, optional_key

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
VALID_PRIVACY = ("private", "unlisted", "public")


class YouTubeError(RuntimeError):
    pass


def _client_secret_path() -> Path:
    load_env()
    override = optional_key("VIDFORGE_YT_CLIENT_SECRET")
    path = Path(override).expanduser() if override else SECRETS_DIR / "client_secret.json"
    if not path.exists():
        raise YouTubeError(
            f"OAuth client secret not found at {path}.\n"
            "Create one in Google Cloud Console (OAuth client ID -> Desktop app), "
            "download the JSON, and save it there. See vidforge/youtube.py for steps."
        )
    return path


def _token_path() -> Path:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    return SECRETS_DIR / "youtube_token.json"


def _service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # noqa: BLE001
        raise YouTubeError(
            "YouTube upload needs extra packages:\n"
            "  uv pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ) from exc

    token_path = _token_path()
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_client_secret_path()), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        token_path.chmod(0o600)

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(
    cfg: Config,
    video: Path,
    meta: dict[str, Any],
    *,
    privacy: str | None = None,
    thumbnail: Path | None = None,
) -> dict[str, Any]:
    """Upload a rendered video. Returns {'video_id', 'url', 'privacy'}."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not video.exists():
        raise YouTubeError(f"video not found: {video}")

    privacy = (privacy or cfg.get("youtube.privacy", "private")).lower()
    if privacy not in VALID_PRIVACY:
        raise YouTubeError(f"privacy must be one of {VALID_PRIVACY}, got {privacy!r}")

    if privacy == "public" and not cfg.get("youtube.enabled", False):
        raise YouTubeError(
            "Refusing to publish publicly: set `youtube.enabled: true` in config.yaml "
            "first. This is a deliberate two-key guard so an unattended run can never "
            "publish to your channel by accident."
        )

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "categoryId": str(cfg.get("youtube.category_id", "27")),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(cfg.get("youtube.made_for_kids", False)),
        },
    }

    service = _service()
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"   uploading… {int(status.progress() * 100)}%")
    except HttpError as exc:  # noqa: BLE001
        raise YouTubeError(f"YouTube rejected the upload: {exc}") from exc

    video_id = response["id"]
    print(f"   uploaded as {privacy}: https://youtu.be/{video_id}")

    if thumbnail and thumbnail.exists():
        try:
            service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg"),
            ).execute()
            print("   thumbnail set")
        except HttpError as exc:  # noqa: BLE001 - needs a verified channel
            print(f"   ! thumbnail not set ({exc}); upload itself succeeded")

    return {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "privacy": privacy,
    }
