"""
main.py — Master CLI for YouTube Auto-Uploader Automation.

Usage:
    # Run pipeline for all configured channels:
    python main.py

    # Run for a specific channel:
    python main.py --channel BillionBuilders

    # Dry-run (preview new videos & channel CSV defaults without downloading/uploading):
    python main.py --dry-run

    # Skip upload phase (download and create ZIP+CSV batches only):
    python main.py --skip-upload

    # Run browser in headless mode:
    python main.py --headless
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import logging
from datetime import datetime

from config import CHANNELS_JSON, LOG_DIR
from yt_downloader import fetch_channel_videos, download_channel_videos
from batcher import create_channel_batches
from uploader import run_batch_upload
from registry import load_registry, update_video_status

# Setup logging
log_filename = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)


def load_channel_configs() -> list[dict]:
    if not os.path.isfile(CHANNELS_JSON):
        log.error(f"Channels config file not found at {CHANNELS_JSON}")
        return []
    try:
        with open(CHANNELS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("channels", [])
    except Exception as e:
        log.error(f"Failed to read channels config: {e}")
        return []


def run_pipeline(
    channel_id_filter: str | None = None,
    max_videos: int | None = None,
    skip_upload: bool = False,
    dry_run: bool = False,
    headless: bool = False,
):
    channels = load_channel_configs()
    if not channels:
        log.error("No channels configured in channels.json.")
        return

    if channel_id_filter:
        channels = [c for c in channels if c["id"].lower() == channel_id_filter.lower()]
        if not channels:
            log.error(f"No channel matching ID '{channel_id_filter}' found in channels.json.")
            return

    log.info(f"Starting YouTube Auto-Uploader pipeline for {len(channels)} channel(s).")
    log.info(f"Flags: dry_run={dry_run}, skip_upload={skip_upload}, headless={headless}")

    summary_results = []

    for ch in channels:
        ch_id = ch["id"]
        ch_name = ch.get("name", ch_id)
        log.info(f"\n========================================================")
        log.info(f" Processing Channel: {ch_name} ({ch_id})")
        log.info(f" Channel CSV Defaults: {json.dumps(ch.get('csv_defaults', {}), indent=2)}")
        log.info(f"========================================================")

        if dry_run:
            discovered = fetch_channel_videos(ch["url"], max_videos=max_videos or ch.get("max_videos_per_run", 20))
            reg = load_registry(ch_id)
            new_vids = [v for v in discovered if vid_not_processed(reg, v["video_id"])]
            log.info(f"[DRY-RUN] Discovered {len(discovered)} total videos, {len(new_vids)} new.")
            for v in new_vids[:5]:
                log.info(f"  - {v['title']} ({v['video_id']})")
            continue

        # 1. Download missing videos
        downloaded = download_channel_videos(ch, max_videos=max_videos or ch.get("max_videos_per_run", 20))
        log.info(f"Downloaded {len(downloaded)} video(s).")

        # 2. Create ZIP + CSV batches
        batches = create_channel_batches(ch)
        log.info(f"Generated {len(batches)} batch(es).")

        # 3. Upload batches to app via Selenium
        uploaded_count = 0
        failed_count = 0

        for b in batches:
            b_name = b["batch_name"]
            csv_path = b["csv_path"]
            zip_path = b["zip_path"]

            if skip_upload:
                log.info(f"Skipping upload for {b_name} (--skip-upload set).")
                continue

            log.info(f"Uploading {b_name} ({b['video_count']} videos, {b['total_bytes'] / (1024*1024):.1f} MB) …")
            upload_res = run_batch_upload(
                batch_name=b_name,
                csv_path=csv_path,
                zip_path=zip_path,
                headless=headless,
            )

            status = upload_res.get("status")
            job_id = upload_res.get("job_id")

            if status == "submitted" and job_id:
                uploaded_count += 1
                log.info(f"✅ Batch {b_name} uploaded & submitted! Job ID: {job_id}")
                for v in b["videos"]:
                    update_video_status(
                        channel_id=ch_id,
                        video_id=v["video_id"],
                        status="uploaded",
                        batch_name=b_name,
                        job_id=job_id
                    )
            else:
                failed_count += 1
                log.error(f"❌ Batch {b_name} upload failed. Reason: {upload_res.get('reason')}")

        summary_results.append({
            "channel": ch_id,
            "downloaded": len(downloaded),
            "batches_created": len(batches),
            "uploaded": uploaded_count,
            "failed": failed_count,
        })

    log.info("\n========================================================")
    log.info(" PIPELINE RUN SUMMARY")
    log.info("========================================================")
    for res in summary_results:
        log.info(f" Channel {res['channel']}: Downloaded={res['downloaded']}, Batches={res['batches_created']}, Uploaded={res['uploaded']}, Failed={res['failed']}")
    log.info("========================================================\n")


def vid_not_processed(registry: dict, vid: str) -> bool:
    rec = registry.get(vid)
    return not rec or rec.get("status") not in ("downloaded", "batched", "uploaded")


def main():
    parser = argparse.ArgumentParser(description="YouTube Channel Auto-Uploader")
    parser.add_argument("--channel", type=str, help="Specific channel ID to process")
    parser.add_argument("--max-videos", type=int, help="Override max videos to fetch per channel")
    parser.add_argument("--skip-upload", action="store_true", help="Download and batch videos without uploading to app")
    parser.add_argument("--dry-run", action="store_true", help="Preview channel videos without downloading or uploading")
    parser.add_argument("--headless", action="store_true", help="Run Selenium browser in headless mode")
    parser.add_argument("--watch", action="store_true", help="Run continuously in a loop at specified interval")
    parser.add_argument("--interval", type=int, default=3600, help="Interval in seconds between watch loop runs (default: 3600 = 1 hour)")

    args = parser.parse_args()

    if args.watch:
        import time
        log.info(f"Starting continuous watch mode. Checking for new videos every {args.interval} seconds.")
        while True:
            try:
                run_pipeline(
                    channel_id_filter=args.channel,
                    max_videos=args.max_videos,
                    skip_upload=args.skip_upload,
                    dry_run=args.dry_run,
                    headless=args.headless,
                )
            except Exception as e:
                log.error(f"Error during watch pipeline execution: {e}")
            log.info(f"Sleeping for {args.interval} seconds until next check...\n")
            time.sleep(args.interval)
    else:
        run_pipeline(
            channel_id_filter=args.channel,
            max_videos=args.max_videos,
            skip_upload=args.skip_upload,
            dry_run=args.dry_run,
            headless=args.headless,
        )


if __name__ == "__main__":
    main()
