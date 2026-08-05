"""
registry.py — Channel tracking and deduplication registry manager.
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime
from config import REGISTRY_DIR

log = logging.getLogger(__name__)


def get_registry_path(channel_id: str) -> str:
    """Get the path to a channel's registry file."""
    return os.path.join(REGISTRY_DIR, f"{channel_id}_registry.json")


def load_registry(channel_id: str) -> dict[str, dict]:
    """
    Load registry dict mapping video_id -> metadata dict.
    """
    path = get_registry_path(channel_id)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Error loading registry for {channel_id}: {e}")
    return {}


def save_registry(channel_id: str, registry_data: dict[str, dict]):
    """
    Save channel registry dict to disk atomically.
    """
    path = get_registry_path(channel_id)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(registry_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def is_video_processed(registry: dict, video_id: str) -> bool:
    """
    Check if a video is already in the registry and marked downloaded/batched/uploaded/ignored_brand/ignored_date.
    """
    rec = registry.get(video_id)
    if not rec:
        return False
    status = rec.get("status")
    return status in ("downloaded", "batched", "uploaded", "ignored_brand", "ignored_date")


def record_video_download(channel_id: str, video_info: dict):
    """
    Record or update a video entry after downloading.
    """
    reg = load_registry(channel_id)
    vid = video_info["video_id"]
    reg[vid] = {
        "video_id": vid,
        "title": video_info.get("title", ""),
        "url": video_info.get("url", ""),
        "file_path": video_info.get("file_path", ""),
        "safe_name": video_info.get("safe_name", ""),
        "file_size": video_info.get("file_size", 0),
        "downloaded_at": datetime.now().isoformat(),
        "status": "downloaded",
        "batch_name": None,
        "job_id": None,
        "uploaded_at": None,
    }
    save_registry(channel_id, reg)


def update_video_status(channel_id: str, video_id: str, status: str, batch_name: str | None = None, job_id: str | None = None):
    """
    Update status of a video in the registry.
    """
    reg = load_registry(channel_id)
    if video_id in reg:
        reg[video_id]["status"] = status
        if batch_name:
            reg[video_id]["batch_name"] = batch_name
        if job_id:
            reg[video_id]["job_id"] = job_id
        if status == "uploaded":
            reg[video_id]["uploaded_at"] = datetime.now().isoformat()
        save_registry(channel_id, reg)
