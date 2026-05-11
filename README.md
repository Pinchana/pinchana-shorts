# 🎬 Pinchana Shorts Scraper

**Pinchana Shorts** is a dedicated YouTube Shorts scraper module built around [yt-dlp](https://github.com/yt-dlp/yt-dlp). It downloads Shorts in **H.264 + AAC**, targets **up to 1080p**, and applies optional size optimization to avoid oversized outputs.

---

## ✨ Key Features

- **yt-dlp-only extraction flow** for YouTube Shorts URLs.
- **Codec preference:** H.264 video + AAC audio (MP4 output).
- **1080p targeting with size guardrails:** format filtering plus optional ffmpeg optimization when bitrate/file size is too high.
- **Secure cookie loading:** reads cookies from a mounted local folder, copies to a temporary runtime file, and never writes back to the mounted source.
- **VPN-aware retries:** integrates with Gluetun IP rotation on rate-limit and bot blocks.

---

## 📡 API Endpoints

### `POST /scrape`
Scrapes and downloads a YouTube Shorts URL.

```json
{
  "url": "https://www.youtube.com/shorts/VIDEO_ID"
}
```

### `GET /health`
Returns service and VPN status.

---

## ⚙️ Configuration

| Variable | Default | Description |
|---|---|---|
| `CACHE_PATH` | `./cache` | Local media cache path |
| `CACHE_MAX_SIZE_GB` | `10.0` | Cache upper size limit |
| `YTDLP_COOKIES_DIR` | `/run/pinchana-cookies` | Read-only mounted cookies directory |
| `YTDLP_COOKIE_FILE` | _(unset)_ | Optional explicit cookie file path override |
| `SHORTS_MAX_MB_PER_MINUTE` | `18.0` | If exceeded, transcode for smaller output |
| `SHORTS_X264_CRF` | `24` | Compression level for optimization |
| `SHORTS_X264_PRESET` | `veryfast` | x264 preset for optimization speed/ratio |
| `SHORTS_MAXRATE` | `2500k` | Video max bitrate target during optimization |
| `SHORTS_AUDIO_BITRATE` | `128k` | AAC bitrate during optimization |

---

## 🔐 Cookie Handling (Read-only & non-exposed)

Recommended pattern:

1. Mount host folder with cookies as read-only (e.g. `./secrets/yt-cookies:/run/pinchana-cookies:ro`)
2. Keep host folder out of git (`secrets/` in `.gitignore`)
3. Service copies selected cookies file to `/tmp` for yt-dlp runtime updates
4. Temp cookie file is removed after each request

This avoids writing to the host-mounted cookie source and keeps cookies out of API responses/metadata.

---

## 🛠 Development

```bash
uv sync
uv run uvicorn pinchana_shorts.main:app --host 0.0.0.0 --port 8083 --reload
```

---

## 📜 License

MIT
