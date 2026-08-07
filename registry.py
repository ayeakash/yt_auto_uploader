"""Channel tracking and deduplication registry manager."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from functools import lru_cache

from config import GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SHEETS_REGISTRY_ID, REGISTRY_DIR

log = logging.getLogger(__name__)

SHEET_COLUMNS = [
    "channel_id",
    "video_id",
    "title",
    "url",
    "published_at",
    "channel_name",
    "status",
    "safe_name",
    "file_path",
    "file_size",
    "download_provider",
    "downloaded_at",
    "batch_name",
    "job_id",
    "uploaded_at",
    "ignored_reason",
    "ignored_at",
    "download_attempts",
    "last_error",
    "last_attempted_at",
    "updated_at",
]
INTEGER_FIELDS = {"file_size", "download_attempts"}


def get_registry_path(channel_id: str) -> str:
    """Get the path to a channel's registry file."""
    return os.path.join(REGISTRY_DIR, f"{channel_id}_registry.json")


def _load_local_registry(channel_id: str) -> dict[str, dict]:
    path = get_registry_path(channel_id)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception as exc:
            log.error("Error loading registry for %s: %s", channel_id, exc)
    return {}


def _save_local_registry(channel_id: str, registry_data: dict[str, dict]) -> None:
    path = get_registry_path(channel_id)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(registry_data, file, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def _sheet_is_configured() -> bool:
    return bool(GOOGLE_SHEETS_REGISTRY_ID and GOOGLE_SERVICE_ACCOUNT_FILE)


@lru_cache(maxsize=1)
def _sheet_worksheet():
    """Authenticate once and return the shared Registry worksheet."""
    if not os.path.isfile(GOOGLE_SERVICE_ACCOUNT_FILE):
        raise RuntimeError(
            f"Google service-account file not found: {GOOGLE_SERVICE_ACCOUNT_FILE}"
        )
    try:
        import gspread
    except ImportError as exc:
        raise RuntimeError("gspread is required for the Google Sheets registry") from exc
    try:
        client = gspread.service_account(filename=GOOGLE_SERVICE_ACCOUNT_FILE)
        return client.open_by_key(GOOGLE_SHEETS_REGISTRY_ID).worksheet("Registry")
    except Exception as exc:
        raise RuntimeError(f"Unable to open the Google Sheets registry: {exc}") from exc


def _sheet_records() -> list[dict]:
    values = _sheet_worksheet().get_all_values()
    if not values:
        return []
    header = values[0]
    missing = [column for column in SHEET_COLUMNS if column not in header]
    if missing:
        raise RuntimeError(f"Google Sheets registry is missing columns: {', '.join(missing)}")
    records = []
    for row in values[1:]:
        padded = row + [""] * (len(header) - len(row))
        record = dict(zip(header, padded))
        if not record.get("channel_id") or not record.get("video_id"):
            continue
        for field in INTEGER_FIELDS:
            value = str(record.get(field, "")).replace(",", "").strip()
            if value:
                try:
                    record[field] = int(value)
                except ValueError:
                    pass
        records.append(record)
    return records


def _write_sheet_channel(channel_id: str, registry_data: dict[str, dict]) -> None:
    worksheet = _sheet_worksheet()
    previous = _sheet_records()
    combined = [row for row in previous if row.get("channel_id") != channel_id]
    now = datetime.now().isoformat()
    for video_id, item in registry_data.items():
        combined.append({
            **item,
            "channel_id": channel_id,
            "video_id": video_id,
            "updated_at": item.get("updated_at") or now,
        })
    combined.sort(key=lambda item: (str(item.get("channel_id", "")), str(item.get("video_id", ""))))
    values = [SHEET_COLUMNS]
    values.extend([
        ["" if record.get(column) is None else record.get(column, "") for column in SHEET_COLUMNS]
        for record in combined
    ])
    try:
        worksheet.update(
            values=values,
            range_name=f"A1:U{len(values)}",
            value_input_option="RAW",
        )
        old_last_row = len(previous) + 1
        if old_last_row > len(values):
            worksheet.batch_clear([f"A{len(values) + 1}:U{old_last_row}"])
    except Exception as exc:
        raise RuntimeError(f"Unable to update the Google Sheets registry: {exc}") from exc


def load_registry(channel_id: str) -> dict[str, dict]:
    """Load a channel registry, using Google Sheets when credentials are configured."""
    if not _sheet_is_configured():
        return _load_local_registry(channel_id)
    records = {
        record["video_id"]: record
        for record in _sheet_records()
        if record.get("channel_id") == channel_id
    }
    _save_local_registry(channel_id, records)
    return records


def save_registry(channel_id: str, registry_data: dict[str, dict]):
    """Save remotely first, then maintain a local JSON cache."""
    if _sheet_is_configured():
        _write_sheet_channel(channel_id, registry_data)
    _save_local_registry(channel_id, registry_data)


def is_video_processed(registry: dict, video_id: str) -> bool:
    """Return whether a video already has a terminal processing status."""
    record = registry.get(video_id)
    if not record:
        return False
    return record.get("status") in (
        "downloaded",
        "batched",
        "uploaded",
        "ignored_brand",
        "ignored_date",
    )


def record_video_download(channel_id: str, video_info: dict):
    """Record or update a video after downloading."""
    registry = load_registry(channel_id)
    video_id = video_info["video_id"]
    now = datetime.now().isoformat()
    registry[video_id] = {
        "video_id": video_id,
        "title": video_info.get("title", ""),
        "url": video_info.get("url", ""),
        "file_path": video_info.get("file_path", ""),
        "safe_name": video_info.get("safe_name", ""),
        "file_size": video_info.get("file_size", 0),
        "published_at": video_info.get("published_at", ""),
        "channel_name": video_info.get("channel_name", ""),
        "download_provider": video_info.get("download_provider", ""),
        "downloaded_at": now,
        "status": "downloaded",
        "batch_name": None,
        "job_id": None,
        "uploaded_at": None,
        "updated_at": now,
    }
    save_registry(channel_id, registry)


def update_video_status(
    channel_id: str,
    video_id: str,
    status: str,
    batch_name: str | None = None,
    job_id: str | None = None,
):
    """Update a video's registry status and associated batch/job metadata."""
    registry = load_registry(channel_id)
    if video_id in registry:
        now = datetime.now().isoformat()
        registry[video_id]["status"] = status
        if batch_name:
            registry[video_id]["batch_name"] = batch_name
        if job_id:
            registry[video_id]["job_id"] = job_id
        if status == "uploaded":
            registry[video_id]["uploaded_at"] = now
        registry[video_id]["updated_at"] = now
        save_registry(channel_id, registry)


def record_download_failure(channel_id: str, video_info: dict, reason: str):
    """Persist a failed attempt while keeping the video eligible for retries."""
    registry = load_registry(channel_id)
    video_id = video_info["video_id"]
    existing = registry.get(video_id, {})
    now = datetime.now().isoformat()
    registry[video_id] = {
        **existing,
        "video_id": video_id,
        "title": video_info.get("title", existing.get("title", "")),
        "url": video_info.get("url", existing.get("url", "")),
        "published_at": video_info.get("published_at", existing.get("published_at", "")),
        "status": "download_failed",
        "download_attempts": int(existing.get("download_attempts", 0)) + 1,
        "last_error": reason[-2000:],
        "last_attempted_at": now,
        "updated_at": now,
    }
    save_registry(channel_id, registry)


def record_ignored_video(channel_id: str, video_info: dict, reason: str):
    """Record a video that is intentionally outside the synchronization scope."""
    registry = load_registry(channel_id)
    video_id = video_info["video_id"]
    now = datetime.now().isoformat()
    registry[video_id] = {
        **registry.get(video_id, {}),
        "video_id": video_id,
        "title": video_info.get("title", ""),
        "url": video_info.get("url", ""),
        "published_at": video_info.get("published_at", ""),
        "channel_name": video_info.get("channel_name", ""),
        "status": "ignored_date",
        "ignored_reason": reason,
        "ignored_at": now,
        "updated_at": now,
    }
    save_registry(channel_id, registry)


def mark_batch_uploaded(channel_id: str, batch_name: str, job_id: str | None):
    """Mark every video in a successfully submitted batch as uploaded."""
    registry = load_registry(channel_id)
    changed = False
    now = datetime.now().isoformat()
    for record in registry.values():
        if record.get("batch_name") == batch_name and record.get("status") == "batched":
            record["status"] = "uploaded"
            record["job_id"] = job_id
            record["uploaded_at"] = now
            record["updated_at"] = now
            changed = True
    if changed:
        save_registry(channel_id, registry)
