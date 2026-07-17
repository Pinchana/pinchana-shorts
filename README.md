# Pinchana YouTube Shorts

This FastAPI module downloads supported public YouTube Shorts with yt-dlp. It prefers H.264 video and AAC audio in MP4, targets up to 1080p, and can transcode output that exceeds the configured size-per-minute threshold.

This module serves `/shorts/` URLs through the normal scrape gateway. Ordinary YouTube and `youtu.be` browser downloads use the separate DLP workflow.

## API

- `POST /scrape` accepts `{"url":"https://www.youtube.com/shorts/VIDEO_ID"}`.
- `GET /health` reports service, VPN, and cookie readiness.

External clients should call the gateway's authenticated `POST /v1/scrape` route.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CACHE_PATH` | `./cache` | Media cache path |
| `CACHE_MAX_SIZE_GB` | `10.0` | Cache size limit |
| `YTDLP_COOKIES_DIR` | `/run/pinchana-cookies` | Read-only source directory for cookie files |
| `YTDLP_COOKIE_FILE` | unset | Optional explicit cookie-file override |
| `SHORTS_MAX_MB_PER_MINUTE` | `18.0` | Threshold for optional transcoding |
| `SHORTS_X264_CRF` | `24` | x264 quality value |
| `SHORTS_X264_PRESET` | `veryfast` | x264 speed/compression preset |
| `SHORTS_MAXRATE` | `2500k` | Maximum target video bitrate |
| `SHORTS_AUDIO_BITRATE` | `128k` | Target AAC bitrate |

Mount the host cookie directory read-only and exclude it from version control. The service copies the selected file to a temporary writable location for yt-dlp and removes that copy after the request.

## Development

```sh
uv sync --frozen
uv run uvicorn pinchana_shorts.main:app --host 0.0.0.0 --port 8083 --reload
```

```sh
# Run from the parent pinchana-api directory.
docker build --file pinchana-shorts/Dockerfile --tag pinchana-shorts:local .
```
