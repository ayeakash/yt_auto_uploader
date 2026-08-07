"""Group downloaded videos into size-constrained ZIP and CSV batches."""
from __future__ import annotations

import csv
import logging
import os
import shutil
import zipfile
import re

from config import ADMIN_CSV_HEADER, BATCHES_DIR, MAX_BATCH_BYTES
from registry import load_registry, update_video_status

log = logging.getLogger(__name__)


def csv_value(value: object, *, preserve_case: bool = False) -> str:
    """Return an ASCII, underscore-separated value safe for CSV/web ingestion."""
    text = str(value or "").encode("ascii", "ignore").decode("ascii").strip()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    if not preserve_case:
        text = "_".join(part.capitalize() for part in text.split("_") if part)
    return text


def video_id_value(value: object) -> str:
    """Preserve a YouTube ID while removing characters unsafe for filenames."""
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))


def recover_completed_batches(channel_config: dict) -> int:
    """Reconcile complete CSV/ZIP artifacts after a crash before registry update."""
    channel_id = channel_config["id"]
    prefix = channel_config.get("batch_prefix", f"Batch_{channel_id[:3]}")
    registry = load_registry(channel_id)
    recovered = 0
    for csv_name in os.listdir(BATCHES_DIR):
        if not csv_name.startswith(f"{prefix}_") or not csv_name.endswith(".csv"):
            continue
        batch_name = csv_name[:-4]
        csv_path = os.path.join(BATCHES_DIR, csv_name)
        zip_path = os.path.join(BATCHES_DIR, f"{batch_name}.zip")
        if not os.path.isfile(zip_path):
            continue
        try:
            with open(csv_path, newline="", encoding="utf-8") as file:
                video_names = {
                    row.get("video_name", "")
                    for row in csv.DictReader(file)
                    if row.get("video_name")
                }
            with zipfile.ZipFile(zip_path) as archive:
                expected_files = {f"{name}.mp4" for name in video_names}
                if not video_names or not expected_files.issubset(set(archive.namelist())):
                    continue
        except (OSError, csv.Error, zipfile.BadZipFile):
            continue

        for video_id, record in registry.items():
            clean_title = csv_value(record.get("safe_name", ""))
            expected_name = f"{clean_title}__{video_id_value(video_id)}"
            # Accept the pre-ID naming scheme too, so older completed artifacts
            # can still be reconciled after upgrading.
            if record.get("status") == "downloaded" and (
                expected_name in video_names or clean_title in video_names
            ):
                update_video_status(channel_id, video_id, "batched", batch_name=batch_name)
                recovered += 1
    if recovered:
        log.warning("Recovered %d registry record(s) from completed batch artifacts", recovered)
    return recovered


def get_next_batch_number(channel_prefix: str) -> int:
    """Find the next available batch sequence number for a channel prefix."""
    os.makedirs(BATCHES_DIR, exist_ok=True)
    max_num = 0
    prefix = f"{channel_prefix}_"
    for item in os.listdir(BATCHES_DIR):
        if item.startswith(prefix):
            name = item.split(".")[0]
            try:
                max_num = max(max_num, int(name.replace(prefix, "")))
            except ValueError:
                pass
    return max_num + 1


def create_channel_batches(channel_config: dict) -> list[dict]:
    """Create upload batches from a channel registry's downloaded videos."""
    channel_id = channel_config["id"]
    prefix = channel_config.get("batch_prefix", f"Batch_{channel_id[:3]}")
    defaults = channel_config.get("csv_defaults", {})

    recover_completed_batches(channel_config)

    registry = load_registry(channel_id)
    unbatched = []
    for video_id, item in registry.items():
        if item.get("status") == "downloaded":
            file_path = item.get("file_path", "")
            if file_path and os.path.isfile(file_path):
                unbatched.append(item)
            else:
                log.warning("File missing for downloaded video %s: %s", video_id, file_path)

    if not unbatched:
        log.info("No unbatched videos for channel %s.", channel_id)
        return []

    log.info("Found %d unbatched video(s) for channel %s.", len(unbatched), channel_id)

    batches_list = []
    current_group = []
    current_size = 0
    for item in unbatched:
        video_size = item.get("file_size") or os.path.getsize(item["file_path"])
        if current_size + video_size > MAX_BATCH_BYTES and current_group:
            batches_list.append(current_group)
            current_group = []
            current_size = 0
        current_group.append(item)
        current_size += video_size
    if current_group:
        batches_list.append(current_group)

    start_num = get_next_batch_number(prefix)
    created_batches = []
    for index, group in enumerate(batches_list):
        batch_name = f"{prefix}_{start_num + index:03d}"
        batch_folder = os.path.join(BATCHES_DIR, batch_name)
        csv_path = os.path.join(BATCHES_DIR, f"{batch_name}.csv")
        zip_path = os.path.join(BATCHES_DIR, f"{batch_name}.zip")
        os.makedirs(batch_folder, exist_ok=True)

        csv_rows = []
        total_batch_size = 0
        batch_video_names: dict[str, str] = {}
        for video in group:
            source = video["file_path"]
            source_name = video.get("safe_name") or os.path.splitext(os.path.basename(source))[0]
            clean_title = csv_value(source_name) or "Untitled_Video"
            clean_video_id = video_id_value(video.get("video_id", ""))
            video_name = f"{clean_title}__{clean_video_id}"
            batch_video_names[video["video_id"]] = video_name
            destination = os.path.join(batch_folder, f"{video_name}.mp4")
            if not os.path.exists(destination):
                shutil.copy2(source, destination)
            total_batch_size += os.path.getsize(destination)
            csv_rows.append([
                video_name,
                csv_value(defaults.get("categories_name", "Entertainment")),
                csv_value(defaults.get("age_groups", "")),
                csv_value(
                    defaults.get("channel_name")
                    or channel_config.get("name")
                    or video.get("channel_name")
                    or channel_id,
                    preserve_case=True,
                ),
                csv_value(defaults.get("tags", "")),
                csv_value(defaults.get("playlist_name", "")),
                csv_value(defaults.get("content_formats", "")),
                csv_value(defaults.get("content_types", "Original")),
                csv_value(defaults.get("language", channel_config.get("language", ""))),
            ])

        with open(csv_path, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(ADMIN_CSV_HEADER)
            writer.writerows(csv_rows)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
            for video in group:
                video_name = batch_video_names[video["video_id"]]
                mp4_path = os.path.join(batch_folder, f"{video_name}.mp4")
                archive.write(mp4_path, f"{video_name}.mp4")

        for video in group:
            update_video_status(
                channel_id=channel_id,
                video_id=video["video_id"],
                status="batched",
                batch_name=batch_name,
            )

        log.info(
            "Created %s: %d video(s), %.1f MB",
            batch_name,
            len(group),
            total_batch_size / (1024 * 1024),
        )
        created_batches.append({
            "batch_name": batch_name,
            "csv_path": csv_path,
            "zip_path": zip_path,
            "video_count": len(group),
            "total_bytes": total_batch_size,
            "videos": group,
        })

    return created_batches


def list_pending_batches(channel_config: dict) -> list[dict]:
    """Rebuild upload work from batched registry records after a restart/failure."""
    channel_id = channel_config["id"]
    grouped: dict[str, list[dict]] = {}
    for record in load_registry(channel_id).values():
        batch_name = record.get("batch_name")
        if record.get("status") == "batched" and batch_name:
            grouped.setdefault(batch_name, []).append(record)

    pending = []
    for batch_name, videos in sorted(grouped.items()):
        csv_path = os.path.join(BATCHES_DIR, f"{batch_name}.csv")
        zip_path = os.path.join(BATCHES_DIR, f"{batch_name}.zip")
        if not os.path.isfile(csv_path) or not os.path.isfile(zip_path):
            log.error("Pending batch %s is missing its CSV or ZIP", batch_name)
            continue
        pending.append({
            "batch_name": batch_name,
            "csv_path": csv_path,
            "zip_path": zip_path,
            "video_count": len(videos),
            "total_bytes": os.path.getsize(zip_path),
            "videos": videos,
        })
    return pending
