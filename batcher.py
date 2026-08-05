"""
batcher.py — Groups downloaded YouTube videos into ZIP + CSV batches using channel-specific CSV properties.
"""
from __future__ import annotations

import os
import csv
import shutil
import zipfile
import logging
from datetime import datetime

from config import BATCHES_DIR, MAX_BATCH_BYTES, ADMIN_CSV_HEADER
from registry import load_registry, update_video_status

log = logging.getLogger(__name__)


def get_next_batch_number(channel_prefix: str) -> int:
    """Find the next available batch sequence number for a given prefix."""
    os.makedirs(BATCHES_DIR, exist_ok=True)
    max_num = 0
    prefix = f"{channel_prefix}_"
    for item in os.listdir(BATCHES_DIR):
        if item.startswith(prefix):
            name = item.split(".")[0]  # strip extension
            try:
                num = int(name.replace(prefix, ""))
                max_num = max(max_num, num)
            except ValueError:
                pass
    return max_num + 1


def create_channel_batches(channel_config: dict) -> list[dict]:
    """
    Process un-batched downloaded videos for a channel into ZIP + CSV batches.

    Returns list of generated batch metadata dicts:
    [{'batch_name', 'csv_path', 'zip_path', 'video_count', 'total_bytes', 'videos': [...]}, ...]
    """
    channel_id = channel_config["id"]
    prefix = channel_config.get("batch_prefix", f"Batch_{channel_id[:3]}")
    defaults = channel_config.get("csv_defaults", {})

    registry = load_registry(channel_id)
    unbatched = []
    for vid, item in registry.items():
        if item.get("status") == "downloaded":
            file_path = item.get("file_path", "")
            if file_path and os.path.isfile(file_path):
                unbatched.append(item)
            else:
                log.warning(f"File missing for downloaded video {vid}: {file_path}")

    if not unbatched:
        log.info(f"No unbatched videos for channel {channel_id}.")
        return []

    log.info(f"Found {len(unbatched)} unbatched video(s) for channel {channel_id}.")

    # Group into size-constrained batches
    batches_list = []
    current_group = []
    current_size = 0

    for item in unbatched:
        v_size = item.get("file_size", os.path.getsize(item["file_path"]))
        if current_size + v_size > MAX_BATCH_BYTES and current_group:
            batches_list.append(current_group)
            current_group = []
            current_size = 0
        current_group.append(item)
        current_size += v_size

    if current_group:
        batches_list.append(current_group)

    start_num = get_next_batch_number(prefix)
    created_batches = []

    for idx, group in enumerate(batches_list):
        batch_num = start_num + idx
        batch_name = f"{prefix}_{batch_num:03d}"
        batch_folder = os.path.join(BATCHES_DIR, batch_name)
        csv_path = os.path.join(BATCHES_DIR, f"{batch_name}.csv")
        zip_path = os.path.join(BATCHES_DIR, f"{batch_name}.zip")

        os.makedirs(batch_folder, exist_ok=True)

        csv_rows = []
        total_batch_size = 0

        # Copy videos to batch folder and build CSV rows using channel csv_defaults
        for v in group:
            src_file = v["file_path"]
            safe_name = v.get("safe_name") or os.path.splitext(os.path.basename(src_file))[0]
            dest_file = os.path.join(batch_folder, f"{safe_name}.mp4")

            if not os.path.exists(dest_file):
                shutil.copy2(src_file, dest_file)

            total_batch_size += os.path.getsize(dest_file)

            # Build row using channel-specific CSV properties
            csv_rows.append([
                safe_name,
                defaults.get("categories_name", "Entertainment"),
                defaults.get("age_groups", ""),
                defaults.get("channel_name", channel_config.get("name", channel_id)),
                defaults.get("tags", ""),
                defaults.get("playlist_name", ""),
                defaults.get("content_formats", ""),
                defaults.get("content_types", "Original"),
                defaults.get("language", "English"),
            ])

        # Write CSV
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(ADMIN_CSV_HEADER)
            writer.writerows(csv_rows)

        # Build ZIP archive
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            for v in group:
                safe_name = v.get("safe_name") or os.path.splitext(os.path.basename(v["file_path"]))[0]
                mp4_in_batch = os.path.join(batch_folder, f"{safe_name}.mp4")
                zf.write(mp4_in_batch, f"{safe_name}.mp4")

        # Update registry status for all videos in this batch
        for v in group:
            update_video_status(
                channel_id=channel_id,
                video_id=v["video_id"],
                status="batched",
                batch_name=batch_name
            )

        log.info(f"Created {batch_name}: {len(group)} video(s), {total_batch_size / (1024*1024):.1f} MB")
        created_batches.append({
            "batch_name": batch_name,
            "csv_path": csv_path,
            "zip_path": zip_path,
            "video_count": len(group),
            "total_bytes": total_batch_size,
            "videos": group,
        })

    return created_batches
