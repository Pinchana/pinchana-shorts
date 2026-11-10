import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pinchana_core.models import ScrapeRequest
from pinchana_shorts.main import process_playlist_item


class YouTubePlaylistItemTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_normal_scraper_with_canonical_single_video_url(self):
        async def singleflight(identifier, operation):
            return await operation()
        for url in ["https://www.youtube.com/watch?v=abcdefghijk&list=PLtest", "https://youtu.be/abcdefghijk"]:
            with patch("pinchana_shorts.main.storage.singleflight", side_effect=singleflight), patch(
                "pinchana_shorts.main._scrape_once", AsyncMock(return_value="result")
            ) as scrape:
                self.assertEqual(await process_playlist_item(ScrapeRequest(url=url)), "result")
                scrape.assert_awaited_once_with("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk")

    async def test_rejects_remote_hosts_and_invalid_video_ids(self):
        for url in ["https://youtube.com.evil.example/watch?v=abcdefghijk", "https://youtube.com/watch?v=bad",
                    "http://youtube.com/watch?v=abcdefghijk"]:
            with self.assertRaises(HTTPException) as failure:
                await process_playlist_item(ScrapeRequest(url=url))
            self.assertEqual(failure.exception.status_code, 400)
