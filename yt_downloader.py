"""
yt_downloader.py — YouTube video downloader using yt-dlp.
"""
from __future__ import annotations

import os
import sys
import re
import json
import logging
import subprocess
from pathlib import Path

from datetime import datetime

from config import DOWNLOADS_DIR, CUTOFF_UPLOAD_DATE
from registry import load_registry, save_registry, record_video_download, is_video_processed

log = logging.getLogger(__name__)


def _yt_dlp_cmd() -> list[str]:
    return [sys.executable, "-m", "yt_dlp", "--no-update"]


def fetch_video_details(video_url: str) -> dict:
    """Fetch tags and upload_date for a specific video using yt-dlp JSON dump."""
    cmd = _yt_dlp_cmd() + [
        "--dump-json",
        "--no-playlist",
        video_url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        tags = data.get("tags") or []
        upload_date = data.get("upload_date") or ""
        return {
            "tags": [str(t) for t in tags],
            "upload_date": str(upload_date),
        }
    except Exception as e:
        log.warning(f"Could not fetch video details for {video_url}: {e}")
        return {"tags": [], "upload_date": ""}


def sanitize_filename(title: str) -> str:
    """Convert YouTube title to a clean, safe filename with underscores."""
    title = title.encode('ascii', 'ignore').decode('ascii')
    title = re.sub(r'[^\w\s-]', '', title)
    title = re.sub(r'[\s-]+', '_', title.strip())
    title = re.sub(r'_+', '_', title).strip('_')
    return title or "untitled_video"


def fetch_channel_videos(channel_url: str, max_videos: int = 20) -> list[dict]:
    """
    Fetch latest videos list from YouTube channel URL without downloading media.
    Returns list of dicts: [{'video_id', 'title', 'url', 'upload_date'}, ...]
    """
    cmd = _yt_dlp_cmd() + [
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end", str(max_videos),
        channel_url,
    ]
    log.info(f"Fetching channel videos from {channel_url} …")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        entries = data.get("entries", [])
        videos = []
        for e in entries:
            vid = e.get("id")
            title = e.get("title", "")
            if vid and title:
                videos.append({
                    "video_id": vid,
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "upload_date": e.get("upload_date", ""),
                })
        log.info(f"Discovered {len(videos)} videos from channel.")
        return videos
    except Exception as e:
        log.error(f"Failed to fetch videos for {channel_url}: {e}")
        return []


def download_channel_videos(channel_config: dict, max_videos: int = 10) -> list[dict]:
    """
    Download missing videos for a given channel config.
    Returns list of downloaded video dicts with file path details.
    """
    channel_id = channel_config["id"]
    channel_url = channel_config["url"]
    ch_download_dir = os.path.join(DOWNLOADS_DIR, channel_id)
    os.makedirs(ch_download_dir, exist_ok=True)

    registry = load_registry(channel_id)
    all_videos = fetch_channel_videos(channel_url, max_videos=channel_config.get("max_videos_per_run", max_videos))

    to_download = []
    for v in all_videos:
        if not is_video_processed(registry, v["video_id"]):
            to_download.append(v)

    if not to_download:
        log.info(f"No new videos to download for channel {channel_id}.")
        return []

    log.info(f"Found {len(to_download)} candidate video(s) to check & download for channel {channel_id}.")
    downloaded_items = []

    for idx, v in enumerate(to_download, 1):
        vid = v["video_id"]
        safe_title = sanitize_filename(v["title"])

        # Fetch detailed metadata (tags and upload_date)
        details = fetch_video_details(v["url"])
        tags = details["tags"]
        upload_date = details["upload_date"] or v.get("upload_date", "")

        # 1. Date cutoff check: ignore videos uploaded before August 1, 2026
        if upload_date and upload_date < CUTOFF_UPLOAD_DATE:
            log.info(f"[{idx}/{len(to_download)}] Skipping '{v['title']}' ({vid}): uploaded on {upload_date} (before cutoff {CUTOFF_UPLOAD_DATE}).")
            current_reg = load_registry(channel_id)
            current_reg[vid] = {
                "video_id": vid,
                "title": v["title"],
                "url": v["url"],
                "upload_date": upload_date,
                "status": "ignored_date",
                "ignored_reason": f"Uploaded before cutoff date {CUTOFF_UPLOAD_DATE}",
                "ignored_at": datetime.now().isoformat(),
            }
            save_registry(channel_id, current_reg)
            continue

        # 2. Tag filter: ignore videos containing "Brand" in tags
        if any("brand" in t.lower() for t in tags):
            log.info(f"[{idx}/{len(to_download)}] Skipping '{v['title']}' ({vid}): contains 'Brand' in tags.")
            current_reg = load_registry(channel_id)
            current_reg[vid] = {
                "video_id": vid,
                "title": v["title"],
                "url": v["url"],
                "upload_date": upload_date,
                "status": "ignored_brand",
                "ignored_reason": "Contains 'Brand' in tags",
                "tags": tags,
                "ignored_at": datetime.now().isoformat(),
            }
            save_registry(channel_id, current_reg)
            continue

        out_template = os.path.join(ch_download_dir, f"{safe_title}_[%(id)s].%(ext)s")

        cmd = _yt_dlp_cmd() + [
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", out_template,
            v["url"],
        ]
        log.info(f"[{idx}/{len(to_download)}] Downloading: {v['title']} ({vid}) …")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            # Locate downloaded mp4 file
            expected_file = os.path.join(ch_download_dir, f"{safe_title}_[{vid}].mp4")
            actual_file = None
            if os.path.isfile(expected_file):
                actual_file = expected_file
            else:
                # Search for file containing vid
                for f in os.listdir(ch_download_dir):
                    if vid in f and f.endswith(".mp4"):
                        actual_file = os.path.join(ch_download_dir, f)
                        break

            if actual_file and os.path.isfile(actual_file):
                file_size = os.path.getsize(actual_file)
                info = {
                    "video_id": vid,
                    "title": v["title"],
                    "url": v["url"],
                    "safe_name": safe_title,
                    "file_path": actual_file,
                    "file_size": file_size,
                }
                record_video_download(channel_id, info)
                downloaded_items.append(info)
                log.info(f"  Successfully downloaded ({file_size / (1024*1024):.1f} MB): {actual_file}")
            else:
                log.error(f"  Downloaded file not found for {vid}")
        except Exception as e:
            log.error(f"  Failed to download video {vid}: {e}")

    return downloaded_items
