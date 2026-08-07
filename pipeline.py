"""Idempotent daily YouTube download → batch → dashboard upload pipeline."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from batcher import create_channel_batches, list_pending_batches
from config import BASE_DIR, BB_PASSWORD, BB_USERNAME, LOG_DIR
from downloader import download_channel_videos
from registry import mark_batch_uploaded
from uploader import run_batch_upload

log = logging.getLogger(__name__)


def configure_logging(verbose: bool = False) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "pipeline.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def load_channels(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
    channels = payload.get("channels")
    if not isinstance(channels, list):
        raise ValueError("channels.json must contain a 'channels' list")
    for index, channel in enumerate(channels):
        if not channel.get("id") or not channel.get("url"):
            raise ValueError(f"Channel entry {index + 1} requires id and url")
        if not isinstance(channel.get("csv_defaults", {}), dict):
            raise ValueError(f"Channel {channel['id']} csv_defaults must be an object")
        if not channel.get("language") and not channel.get("csv_defaults", {}).get("language"):
            raise ValueError(f"Channel {channel['id']} requires language")
    return channels


def run_channel(
    channel: dict,
    *,
    dry_run: bool,
    skip_download: bool,
    skip_upload: bool,
    headless: bool,
) -> dict:
    channel_id = channel["id"]
    downloaded = []
    if not skip_download:
        downloaded = download_channel_videos(channel, dry_run=dry_run)
    if dry_run:
        return {
            "channel": channel_id,
            "candidates": len(downloaded),
            "downloaded": 0,
            "batches": 0,
            "uploaded": 0,
            "failed": 0,
        }

    created = create_channel_batches(channel)
    pending = list_pending_batches(channel)
    if skip_upload:
        return {
            "channel": channel_id,
            "candidates": 0,
            "downloaded": len(downloaded),
            "batches": len(created),
            "uploaded": 0,
            "failed": 0,
            "pending_upload": len(pending),
        }

    if not BB_USERNAME or not BB_PASSWORD:
        raise RuntimeError("BB_USERNAME and BB_PASSWORD are required for dashboard upload")

    uploaded = 0
    failed = 0
    for batch in pending:
        result = run_batch_upload(
            batch_name=batch["batch_name"],
            csv_path=batch["csv_path"],
            zip_path=batch["zip_path"],
            headless=headless,
        )
        if result.get("status") == "submitted":
            mark_batch_uploaded(channel_id, batch["batch_name"], result.get("job_id"))
            uploaded += 1
        else:
            failed += 1
            log.error("Batch %s failed: %s", batch["batch_name"], result.get("reason"))

    return {
        "channel": channel_id,
        "candidates": 0,
        "downloaded": len(downloaded),
        "batches": len(created),
        "uploaded": uploaded,
        "failed": failed,
    }


def run_pipeline(args: argparse.Namespace) -> int:
    channels = load_channels(args.config)
    if args.channel:
        channels = [channel for channel in channels if channel["id"] == args.channel]
        if not channels:
            raise ValueError(f"Unknown channel id: {args.channel}")
    if not channels:
        log.warning("No channels configured in %s", args.config)
        return 0

    results = []
    for channel in channels:
        try:
            result = run_channel(
                channel,
                dry_run=args.dry_run,
                skip_download=args.skip_download,
                skip_upload=args.skip_upload,
                headless=args.headless,
            )
        except Exception as exc:
            log.exception("Channel %s failed", channel["id"])
            result = {"channel": channel["id"], "failed": 1, "error": str(exc)}
        results.append(result)
        log.info("Channel result: %s", json.dumps(result, sort_keys=True))
    return 1 if any(result.get("failed", 0) for result in results) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=os.path.join(BASE_DIR, "channels.json"),
        help="Path to channel and CSV settings",
    )
    parser.add_argument("--channel", help="Run only one configured channel id")
    parser.add_argument("--dry-run", action="store_true", help="Discover without writing or uploading")
    parser.add_argument("--skip-download", action="store_true", help="Batch/upload existing work only")
    parser.add_argument("--skip-upload", action="store_true", help="Download and batch without dashboard upload")
    parser.add_argument("--headless", action="store_true", help="Run Chrome without a visible window")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    configure_logging(args.verbose)
    lock_path = os.path.join(BASE_DIR, ".pipeline.lock")
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("Another pipeline run is already active")
            return 2
        try:
            return run_pipeline(args)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


if __name__ == "__main__":
    sys.exit(main())
