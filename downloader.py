"""Resilient YouTube discovery and media downloading with provider fallbacks."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

from config import DOWNLOAD_RETRIES, DOWNLOADS_DIR, MIN_PUBLISHED_DATE, YT_COOKIES_FILE
from registry import (
    is_video_processed,
    load_registry,
    record_download_failure,
    record_ignored_video,
    record_video_download,
)
from youtube_api import YouTubeAPIError, YouTubeDataAPI

log = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    """Raised when a provider cannot produce a valid media file."""


def sanitize_filename(title: str) -> str:
    title = title.encode("ascii", "ignore").decode("ascii")
    title = re.sub(r"[^\w\s-]", "", title)
    title = re.sub(r"[\s-]+", "_", title.strip())
    title = re.sub(r"_+", "_", title).strip("_")
    return "_".join(part.capitalize() for part in title.split("_") if part)[:150] or "Untitled_Video"


def parse_published_date(value: object) -> date | None:
    """Parse API ISO timestamps, yt-dlp YYYYMMDD dates, or Unix timestamps."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.isdigit() and len(text) == 8:
            return datetime.strptime(text, "%Y%m%d").date()
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc).date()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (ValueError, OverflowError, OSError):
        return None


def partition_by_cutoff(videos: list[dict], cutoff: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Partition videos into eligible, too-old, and unknown-date groups."""
    cutoff_date = date.fromisoformat(cutoff)
    eligible = []
    too_old = []
    unknown = []
    for video in videos:
        published = parse_published_date(video.get("published_at"))
        if published is None:
            unknown.append(video)
        elif published < cutoff_date:
            too_old.append(video)
        else:
            eligible.append(video)
    return eligible, too_old, unknown


def _run(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _validate_media(path: str) -> None:
    if not os.path.isfile(path) or os.path.getsize(path) < 1024:
        raise DownloadError(f"Downloaded file is missing or too small: {path}")
    if not shutil.which("ffprobe"):
        return
    try:
        result = _run([
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            path,
        ], timeout=60)
        stream_types = {stream.get("codec_type") for stream in json.loads(result.stdout)["streams"]}
    except Exception as exc:
        raise DownloadError(f"ffprobe validation failed for {path}: {exc}") from exc
    if not {"video", "audio"}.issubset(stream_types):
        raise DownloadError(f"Media must contain video and audio streams: {path}")


def discover_with_ytdlp(channel_config: dict, limit: int = 50) -> list[dict]:
    command = [
        sys.executable,
        "-m", "yt_dlp",
        "--no-update",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end", str(min(max(limit, 1), 50)),
        channel_config["url"],
    ]
    try:
        payload = json.loads(_run(command, timeout=120).stdout)
    except Exception as exc:
        raise DownloadError(f"yt-dlp channel discovery failed: {exc}") from exc
    videos = []
    channel_name = str(payload.get("channel") or payload.get("uploader") or channel_config.get("name") or "")
    for entry in payload.get("entries", []):
        video_id = entry.get("id")
        title = entry.get("title")
        if video_id and title:
            videos.append({
                "video_id": str(video_id),
                "title": str(title),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published_at": str(entry.get("timestamp") or entry.get("upload_date") or ""),
                "channel_name": str(entry.get("channel") or channel_name),
            })
    return videos


def discover_latest(channel_config: dict, limit: int = 50) -> tuple[list[dict], str]:
    """Use the Data API first, retaining yt-dlp as a no-key/outage fallback."""
    try:
        return YouTubeDataAPI().latest_uploads(channel_config, limit), "youtube_data_api"
    except YouTubeAPIError as exc:
        log.warning("Data API discovery unavailable for %s: %s", channel_config["id"], exc)
        return discover_with_ytdlp(channel_config, limit), "yt_dlp"


class YtDlpProvider:
    name = "yt_dlp"

    def download(self, video: dict, output_dir: str) -> str:
        safe_title = sanitize_filename(video["title"])
        template = os.path.join(output_dir, f"{safe_title}_[%(id)s].%(ext)s")
        command = [
            sys.executable,
            "-m", "yt_dlp",
            "--no-update",
            "--no-playlist",
            "--continue",
            "--no-overwrites",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "http:exp=1:20",
            "--retry-sleep", "fragment:exp=1:20",
            "--concurrent-fragments", "4",
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "--print", "after_move:filepath",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "-o", template,
        ]
        if YT_COOKIES_FILE and os.path.isfile(YT_COOKIES_FILE):
            command.extend(["--cookies", YT_COOKIES_FILE])
        command.append(video["url"])
        try:
            result = _run(command, timeout=60 * 60)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc))[-3000:]
            raise DownloadError(detail) from exc
        except Exception as exc:
            raise DownloadError(str(exc)) from exc

        candidates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        candidates.extend(
            str(path)
            for path in Path(output_dir).glob("*.mp4")
            if f"_[{video['video_id']}]" in path.name
        )
        for candidate in reversed(candidates):
            if os.path.isfile(candidate):
                _validate_media(candidate)
                return candidate
        raise DownloadError("yt-dlp completed but its MP4 output was not found")


class PytubeFixProvider:
    name = "pytubefix"

    def download(self, video: dict, output_dir: str) -> str:
        try:
            from pytubefix import YouTube
        except ImportError as exc:
            raise DownloadError("pytubefix is not installed") from exc

        safe_title = sanitize_filename(video["title"])
        final_path = os.path.join(output_dir, f"{safe_title}_[{video['video_id']}].mp4")
        try:
            youtube = YouTube(video["url"])
            adaptive_video = (
                youtube.streams.filter(adaptive=True, only_video=True, file_extension="mp4")
                .order_by("resolution").desc().first()
            )
            adaptive_audio = (
                youtube.streams.filter(only_audio=True, file_extension="mp4")
                .order_by("abr").desc().first()
            )
            with tempfile.TemporaryDirectory(prefix="pytubefix-", dir=output_dir) as temp_dir:
                if adaptive_video and adaptive_audio and shutil.which("ffmpeg"):
                    video_path = adaptive_video.download(temp_dir, filename="video.mp4")
                    audio_path = adaptive_audio.download(temp_dir, filename="audio.m4a")
                    _run([
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", video_path,
                        "-i", audio_path,
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-c", "copy",
                        "-movflags", "+faststart",
                        final_path,
                    ], timeout=60 * 30)
                else:
                    progressive = (
                        youtube.streams.filter(progressive=True, file_extension="mp4")
                        .order_by("resolution").desc().first()
                    )
                    if not progressive:
                        raise DownloadError("pytubefix found no usable MP4 stream")
                    temp_path = progressive.download(temp_dir, filename="progressive.mp4")
                    os.replace(temp_path, final_path)
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(str(exc)) from exc
        _validate_media(final_path)
        return final_path


def _existing_download(output_dir: str, video_id: str) -> str | None:
    for path in Path(output_dir).glob("*.mp4"):
        if f"_[{video_id}]" not in path.name:
            continue
        try:
            _validate_media(str(path))
            return str(path)
        except DownloadError:
            log.warning("Ignoring invalid existing download: %s", path)
            quarantine = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
            try:
                path.replace(quarantine)
                log.warning("Quarantined invalid media as %s", quarantine)
            except OSError as exc:
                log.warning("Could not quarantine invalid media %s: %s", path, exc)
    return None


def download_channel_videos(channel_config: dict, dry_run: bool = False) -> list[dict]:
    """Discover and download unprocessed channel uploads, oldest first."""
    channel_id = channel_config["id"]
    output_dir = os.path.join(DOWNLOADS_DIR, channel_id)
    os.makedirs(output_dir, exist_ok=True)
    discovery_limit = int(channel_config.get("discovery_limit", 50))
    videos, discovery_provider = discover_latest(channel_config, discovery_limit)
    registry = load_registry(channel_id)
    unprocessed = [video for video in videos if not is_video_processed(registry, video["video_id"])]
    pending, too_old, unknown_date = partition_by_cutoff(unprocessed, MIN_PUBLISHED_DATE)
    if not dry_run:
        for video in too_old:
            record_ignored_video(
                channel_id,
                video,
                f"Published before cutoff {MIN_PUBLISHED_DATE}",
            )
    if too_old:
        log.info("%s: ignored %d video(s) published before %s", channel_id, len(too_old), MIN_PUBLISHED_DATE)
    if unknown_date:
        log.warning(
            "%s: skipped %d video(s) with unknown publication dates",
            channel_id,
            len(unknown_date),
        )
    pending.reverse()
    pending = pending[: int(channel_config.get("max_videos_per_run", 20))]
    log.info(
        "%s: discovered %d via %s; %d eligible pending",
        channel_id, len(videos), discovery_provider, len(pending),
    )
    if dry_run:
        return pending

    downloaded = []
    providers = [YtDlpProvider(), PytubeFixProvider()]
    for video in pending:
        existing = _existing_download(output_dir, video["video_id"])
        if existing:
            chosen_path = existing
            provider_name = "existing"
        else:
            chosen_path = ""
            provider_name = ""
            errors = []
            for provider in providers:
                for attempt in range(1, DOWNLOAD_RETRIES + 1):
                    try:
                        log.info(
                            "%s: downloading %s with %s (%d/%d)",
                            channel_id, video["video_id"], provider.name, attempt, DOWNLOAD_RETRIES,
                        )
                        chosen_path = provider.download(video, output_dir)
                        provider_name = provider.name
                        break
                    except DownloadError as exc:
                        errors.append(f"{provider.name} attempt {attempt}: {exc}")
                        if attempt < DOWNLOAD_RETRIES:
                            time.sleep(min(2 ** attempt, 30))
                if chosen_path:
                    break
            if not chosen_path:
                reason = " | ".join(errors)
                record_download_failure(channel_id, video, reason)
                log.error("%s: all download providers failed for %s", channel_id, video["video_id"])
                continue

        info = {
            **video,
            "safe_name": sanitize_filename(video["title"]),
            "file_path": chosen_path,
            "file_size": os.path.getsize(chosen_path),
            "download_provider": provider_name,
        }
        record_video_download(channel_id, info)
        downloaded.append(info)
    return downloaded
