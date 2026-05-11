"""YouTube Shorts scraper plugin powered by yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from pinchana_core.models import ScrapeRequest, ScrapeResponse
from pinchana_core.plugins import ScraperPlugin, registry
from pinchana_core.storage import MediaStorage
from pinchana_core.vpn import GluetunController, VpnRotationError
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()
gluetun = GluetunController()
storage = MediaStorage(
    base_path=os.getenv("CACHE_PATH", "./cache"),
    max_size_gb=float(os.getenv("CACHE_MAX_SIZE_GB", "10.0")),
)

# Use yt-dlp's native best-quality selection with codec/res preferences.
# Mirrors the `-t mp4` preset: H.264 video + AAC audio, up to 1080p, MP4 container.
# `res` is the *smaller* dimension, so res:1080 correctly matches both
# landscape 1920x1080 and portrait 1080x1920 (Shorts).
# Force separate video-only + audio-only streams so yt-dlp downloads the
# highest-quality H.264/AAC DASH tracks and merges them. Fallback to best
# combined format only when separate streams are unavailable.
SHORTS_FORMAT = "bv+ba/b"

# Mirror yt-dlp's `-t mp4` preset sort order.
# `res:1080` caps at 1080p (YouTube doesn't serve H.264 above 1080p).
SHORTS_FORMAT_SORT = [
    "vcodec:h264",
    "acodec:aac",
    "res:1080",
    "lang",
    "quality",
    "fps",
    "hdr:12",
    "size",
    "br",
]


def _safe_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _media_url_to_path(url: str | None):
    if not url:
        return None
    url = str(url)
    if not url.startswith("/media/"):
        return None
    path_part = url.split("?", 1)[0][len("/media/"):]
    parts = path_part.split("/", 2)
    if len(parts) < 3:
        return None
    platform, post_id, filename = parts[0], parts[1], parts[2]
    if platform != "shorts" or not post_id or not filename:
        return None
    return storage.base_path / post_id / filename


def _cached_media_ready(metadata: dict) -> bool:
    if not isinstance(metadata, dict):
        return False

    video_path = _media_url_to_path(metadata.get("video_url"))
    if not video_path or not video_path.exists():
        return False

    thumb_url = metadata.get("thumbnail_url")
    if thumb_url:
        thumb_path = _media_url_to_path(thumb_url)
        if not thumb_path or not thumb_path.exists():
            return False

    return True


def _extract_short_id(url: str) -> str | None:
    for pattern in (
        r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})",
        r"youtu\.be/([A-Za-z0-9_-]{6,})",
        r"[?&]v=([A-Za-z0-9_-]{6,})",
    ):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _resolve_cookie_source() -> Path | None:
    cookie_file_env = os.getenv("YTDLP_COOKIE_FILE", "").strip()
    if cookie_file_env:
        path = Path(cookie_file_env)
        if path.is_file():
            return path
        logger.warning("Cookie file not found: %s", path)
        return None

    cookies_dir = Path(os.getenv("YTDLP_COOKIES_DIR", "/run/pinchana-cookies"))
    if not cookies_dir.exists() or not cookies_dir.is_dir():
        return None

    for name in ("youtube.com_cookies.txt", "cookies.txt"):
        candidate = cookies_dir / name
        if candidate.is_file():
            return candidate

    candidates = sorted(p for p in cookies_dir.glob("*.txt") if p.is_file())
    return candidates[0] if candidates else None


def _prepare_runtime_cookiefile(source: Path | None) -> Path | None:
    if source is None:
        return None

    tmp_dir = Path(os.getenv("YTDLP_COOKIE_TMP_DIR", "/tmp"))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_cookie = tmp_dir / f"ytcookies-{uuid.uuid4().hex}.txt"
    shutil.copy2(source, tmp_cookie)
    os.chmod(tmp_cookie, 0o600)
    return tmp_cookie


def _cleanup_temp_cookiefile(path: Path | None) -> None:
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed cleaning temporary cookie file")


def _find_downloaded_file(base_dir: Path, prefix: str) -> Path | None:
    matches = sorted(p for p in base_dir.glob(f"{prefix}.*") if p.is_file())
    return matches[0] if matches else None


def _optimize_if_needed(video_path: Path, duration_seconds: float | int | None) -> Path:
    """No-op: we prioritize quality over file size. Keep original H.264/AAC stream."""
    return video_path


def _download_short(url: str, post_dir: Path, cookiefile: Path | None) -> tuple[dict, Path | None, Path | None]:
    outtmpl = {
        "default": str(post_dir / "video.%(ext)s"),
        "thumbnail": str(post_dir / "thumbnail.%(ext)s"),
    }
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "overwrites": True,
        "outtmpl": outtmpl,
        "writethumbnail": True,
        "format": SHORTS_FORMAT,
        "format_sort": SHORTS_FORMAT_SORT,
        "format_sort_force": True,
        "merge_output_format": "mp4",
        "remux_video": "mp4",
        "retries": 2,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 1,
    }
    if cookiefile:
        ydl_opts["cookiefile"] = str(cookiefile)

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=True))

    video_file = _find_downloaded_file(post_dir, "video")
    thumb_file = _find_downloaded_file(post_dir, "thumbnail")
    if video_file:
        _optimize_if_needed(video_file, info.get("duration"))

    return info, video_file, thumb_file


class RateLimitError(Exception):
    """Raised when YouTube blocks/rate-limits requests."""


async def trigger_rotation():
    """Trigger VPN IP rotation."""
    logger.warning("Rotating VPN IP...")
    try:
        await gluetun.rotate_ip()
    except VpnRotationError as e:
        logger.warning("VPN rotation failed: %s", e)
        raise RateLimitError(str(e))


def _is_rate_limited(e: Exception) -> bool:
    msg = str(e).lower()
    return any(
        x in msg
        for x in (
            "403",
            "429",
            "too many requests",
            "rate limit",
            "captcha",
            "bot",
            "sign in to confirm",
            "unavailable",
        )
    )


async def _scrape_once(url: str, short_id_hint: str | None) -> ScrapeResponse:
    cookie_source = _resolve_cookie_source()
    runtime_cookie = _prepare_runtime_cookiefile(cookie_source)
    try:
        short_id = short_id_hint or _extract_short_id(url) or f"yt-{uuid.uuid4().hex[:10]}"

        if storage.is_cached(short_id):
            cached = storage.load_metadata(short_id)
            if cached and _cached_media_ready(cached):
                logger.info("Cache hit for %s", short_id)
                return ScrapeResponse(**cached)
            logger.info("Cache invalid for %s, missing media; re-scraping", short_id)

        storage.prepare_post_dir(short_id)
        post_dir = storage._post_dir(short_id)

        info, video_file, thumb_file = await asyncio.to_thread(_download_short, url, post_dir, runtime_cookie)

        real_short_id = info.get("id") or short_id
        if real_short_id != short_id:
            real_dir = storage._post_dir(real_short_id)
            if real_dir.exists():
                shutil.rmtree(real_dir)
            post_dir.replace(real_dir)
            post_dir = real_dir
            short_id = real_short_id

        video_file = _find_downloaded_file(post_dir, "video")
        thumb_file = _find_downloaded_file(post_dir, "thumbnail")

        if not video_file:
            raise HTTPException(status_code=503, detail="Media download failed")

        video_ext = video_file.suffix.lstrip(".") or "mp4"
        thumb_ext = thumb_file.suffix.lstrip(".") if thumb_file else "jpg"

        response = ScrapeResponse(
            shortcode=short_id,
            caption=info.get("title") or info.get("description") or short_id,
            author=info.get("uploader") or info.get("channel") or "",
            media_type="video",
            thumbnail_url=f"/media/shorts/{short_id}/thumbnail.{thumb_ext}" if thumb_file else "",
            video_url=f"/media/shorts/{short_id}/video.{video_ext}",
            audio_url=None,
            carousel=None,
        )

        storage.save_metadata(short_id, response.model_dump())
        return response
    finally:
        _cleanup_temp_cookiefile(runtime_cookie)


@router.post("/scrape", response_model=ScrapeResponse)
async def process_scrape_request(request: ScrapeRequest):
    url = str(request.url)
    if "youtube.com/shorts/" not in url and "youtu.be/" not in url:
        raise HTTPException(status_code=400, detail="Only YouTube Shorts URLs are supported")

    short_id = _extract_short_id(url)
    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            logger.info("Scraping YouTube Shorts: %s (attempt %d)", short_id or url, attempt)
            return await _scrape_once(url, short_id)
        except HTTPException as e:
            last_error = e
            if e.status_code < 500:
                raise
            if attempt < 3:
                await asyncio.sleep(2)
        except Exception as e:
            last_error = e
            if _is_rate_limited(e):
                logger.warning("Attempt %d rate-limited/blocked: %s", attempt, e)
                if attempt < 3:
                    try:
                        await trigger_rotation()
                    except RateLimitError:
                        await asyncio.sleep(30)
                    else:
                        await asyncio.sleep(5)
            else:
                logger.error("Attempt %d failed: %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(5)

    if isinstance(last_error, HTTPException):
        raise last_error
    raise HTTPException(
        status_code=503 if last_error and _is_rate_limited(last_error) else 500,
        detail=str(last_error) if last_error else "Unknown scrape error",
    )


@router.get("/health")
async def health_check():
    try:
        status = await gluetun.get_vpn_status()
        vpn_status = status.get("status", "").lower()
        if vpn_status != "running":
            raise HTTPException(status_code=503, detail=f"VPN not running: {vpn_status}")
        return {
            "status": "healthy",
            "service": "shorts",
            "vpn": status,
            "cookies_available": _resolve_cookie_source() is not None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"VPN check failed: {e}")


registry.register(
    ScraperPlugin(
        name="shorts",
        router=router,
        route_patterns=[
            "youtube.com/shorts",
            "www.youtube.com/shorts",
            "m.youtube.com/shorts",
        ],
    )
)

app = FastAPI(title="Pinchana Shorts", version="0.1.0")
app.include_router(router)
