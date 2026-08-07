from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import batcher
import registry
from downloader import parse_published_date, partition_by_cutoff, sanitize_filename
from youtube_api import YouTubeDataAPI


class FakeYouTubeAPI(YouTubeDataAPI):
    def __init__(self):
        super().__init__(["test-key"])
        self.requests = []

    def _get(self, resource: str, params: dict) -> dict:
        self.requests.append((resource, params))
        if resource == "channels":
            return {
                "items": [{
                    "id": "UC123",
                    "snippet": {"title": "Resolved Channel"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
                }]
            }
        return {
            "items": [
                {
                    "snippet": {
                        "title": "Latest video",
                        "publishedAt": "2026-08-07T01:00:00Z",
                        "resourceId": {"videoId": "video-1"},
                    },
                    "contentDetails": {
                        "videoId": "video-1",
                        "videoPublishedAt": "2026-08-07T00:59:00Z",
                    },
                },
                {
                    "snippet": {"title": "Private video"},
                    "contentDetails": {"videoId": "private-1"},
                },
            ]
        }


class CoreTests(unittest.TestCase):
    def test_data_api_resolves_handle_and_returns_dates(self):
        api = FakeYouTubeAPI()
        videos = api.latest_uploads({"id": "demo", "url": "https://youtube.com/@Demo"})
        self.assertEqual([video["video_id"] for video in videos], ["video-1"])
        self.assertEqual(videos[0]["published_at"], "2026-08-07T00:59:00Z")
        self.assertEqual(videos[0]["channel_name"], "Resolved Channel")
        self.assertEqual(api.requests[0][1]["forHandle"], "Demo")
        self.assertEqual(api.requests[1][1]["playlistId"], "UU123")

    def test_sanitize_filename_is_stable_and_safe(self):
        self.assertEqual(sanitize_filename(" Kids: Share 🍬 -- Now! "), "Kids_Share_Now")

    def test_august_2026_cutoff(self):
        videos = [
            {"video_id": "old", "published_at": "2026-07-31T23:59:59Z"},
            {"video_id": "boundary", "published_at": "2026-08-01T00:00:00Z"},
            {"video_id": "new", "published_at": "20260807"},
            {"video_id": "unknown", "published_at": ""},
        ]
        eligible, old, unknown = partition_by_cutoff(videos, "2026-08-01")
        self.assertEqual([item["video_id"] for item in eligible], ["boundary", "new"])
        self.assertEqual([item["video_id"] for item in old], ["old"])
        self.assertEqual([item["video_id"] for item in unknown], ["unknown"])
        self.assertEqual(str(parse_published_date("1786032000")), "2026-08-06")

    def test_batch_is_recoverable_and_marked_uploaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batches_dir = root / "batches"
            registries_dir = root / "registries"
            batches_dir.mkdir()
            registries_dir.mkdir()
            video = root / "video.mp4"
            video.write_bytes(b"video-data")

            with (
                patch.object(batcher, "BATCHES_DIR", str(batches_dir)),
                patch.object(registry, "REGISTRY_DIR", str(registries_dir)),
            ):
                registry.record_video_download("demo", {
                    "video_id": "v1",
                    "title": "Video",
                    "url": "https://youtube.com/watch?v=v1",
                    "safe_name": "Video",
                    "file_path": str(video),
                    "file_size": video.stat().st_size,
                })
                config = {
                    "id": "demo",
                    "name": "Canonical Demo Name",
                    "batch_prefix": "Batch_DM",
                    "language": "Hindi",
                    "csv_defaults": {},
                }
                created = batcher.create_channel_batches(config)
                self.assertEqual(len(created), 1)
                with zipfile.ZipFile(created[0]["zip_path"]) as archive:
                    self.assertEqual(archive.namelist(), ["Video__v1.mp4"])
                csv_text = Path(created[0]["csv_path"]).read_text()
                self.assertIn("Entertainment", csv_text)
                self.assertIn("Canonical_Demo_Name", csv_text)
                self.assertIn("Original", csv_text)
                self.assertIn("Hindi", csv_text)

                pending = batcher.list_pending_batches(config)
                self.assertEqual([item["batch_name"] for item in pending], ["Batch_DM_001"])
                registry.mark_batch_uploaded("demo", "Batch_DM_001", "job-1")
                self.assertEqual(batcher.list_pending_batches(config), [])
                record = registry.load_registry("demo")["v1"]
                self.assertEqual(record["status"], "uploaded")
                self.assertEqual(record["job_id"], "job-1")

    def test_failed_download_remains_retryable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(registry, "REGISTRY_DIR", temp_dir):
                registry.record_download_failure(
                    "demo",
                    {"video_id": "v1", "title": "Video", "url": "url"},
                    "temporary failure",
                )
                data = registry.load_registry("demo")
                self.assertFalse(registry.is_video_processed(data, "v1"))
                self.assertEqual(data["v1"]["download_attempts"], 1)

    def test_completed_batch_is_reconciled_after_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batches_dir = root / "batches"
            registries_dir = root / "registries"
            batches_dir.mkdir()
            registries_dir.mkdir()
            video = root / "video.mp4"
            video.write_bytes(b"video-data")
            config = {"id": "demo", "batch_prefix": "Batch_DM", "csv_defaults": {}}

            with (
                patch.object(batcher, "BATCHES_DIR", str(batches_dir)),
                patch.object(registry, "REGISTRY_DIR", str(registries_dir)),
            ):
                registry.record_video_download("demo", {
                    "video_id": "v1",
                    "title": "Video",
                    "url": "url",
                    "safe_name": "Video",
                    "file_path": str(video),
                    "file_size": video.stat().st_size,
                })
                csv_path = batches_dir / "Batch_DM_001.csv"
                csv_path.write_text(
                    ",".join(batcher.ADMIN_CSV_HEADER) + "\n"
                    + "Video__v1,Entertainment,,,,,,Original,English\n"
                )
                with zipfile.ZipFile(batches_dir / "Batch_DM_001.zip", "w") as archive:
                    archive.writestr("Video__v1.mp4", b"video-data")

                self.assertEqual(batcher.recover_completed_batches(config), 1)
                record = registry.load_registry("demo")["v1"]
                self.assertEqual(record["status"], "batched")
                self.assertEqual(record["batch_name"], "Batch_DM_001")
                self.assertEqual(batcher.create_channel_batches(config), [])


if __name__ == "__main__":
    unittest.main()
