"""Efficient latest-upload discovery through YouTube Data API v3."""
from __future__ import annotations

import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from config import YOUTUBE_API_KEYS

log = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
ROTATABLE_REASONS = {
    "dailyLimitExceeded",
    "keyInvalid",
    "quotaExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}


class YouTubeAPIError(RuntimeError):
    """Raised when the Data API cannot satisfy a discovery request."""


def _error_reason(payload: dict) -> str:
    errors = payload.get("error", {}).get("errors", [])
    if errors:
        return str(errors[0].get("reason", ""))
    return ""


class YouTubeDataAPI:
    def __init__(self, api_keys: list[str] | None = None, timeout: int = 20):
        self.api_keys = list(api_keys if api_keys is not None else YOUTUBE_API_KEYS)
        self.timeout = timeout

    def _get(self, resource: str, params: dict) -> dict:
        if not self.api_keys:
            raise YouTubeAPIError("No YouTube API key configured")

        last_error = "YouTube Data API request failed"
        for key_index, api_key in enumerate(self.api_keys):
            query = urlencode({**params, "key": api_key})
            request = Request(
                f"{API_BASE}/{resource}?{query}",
                headers={"Accept": "application/json", "User-Agent": "daily-video-sync/1.0"},
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except HTTPError as exc:
                try:
                    payload = json.loads(exc.read().decode("utf-8", "replace"))
                except Exception:
                    payload = {}
                reason = _error_reason(payload)
                message = payload.get("error", {}).get("message", str(exc))
                last_error = f"HTTP {exc.code}: {reason or message}"
                if reason in ROTATABLE_REASONS and key_index + 1 < len(self.api_keys):
                    log.warning("YouTube API key %d failed with %s; rotating", key_index + 1, reason)
                    continue
                raise YouTubeAPIError(last_error) from exc
            except URLError as exc:
                raise YouTubeAPIError(f"YouTube Data API network error: {exc}") from exc
        raise YouTubeAPIError(last_error)

    def _channel_lookup(self, channel_config: dict) -> tuple[str, str, str]:
        channel_id = str(channel_config.get("channel_id", "")).strip()
        channel_url = str(channel_config.get("url", "")).strip()
        params = {"part": "contentDetails,snippet"}

        if not channel_id:
            match = re.search(r"/channel/(UC[\w-]+)", channel_url)
            if match:
                channel_id = match.group(1)

        if channel_id:
            params["id"] = channel_id
        else:
            path = urlparse(channel_url).path
            handle_match = re.search(r"/@([^/]+)", path)
            handle = str(channel_config.get("handle", "")).strip()
            if not handle and handle_match:
                handle = handle_match.group(1)
            if not handle:
                raise YouTubeAPIError(
                    f"Channel {channel_config.get('id', '<unknown>')} needs channel_id or @handle URL"
                )
            params["forHandle"] = handle.lstrip("@")

        payload = self._get("channels", params)
        items = payload.get("items", [])
        if not items:
            raise YouTubeAPIError(f"YouTube channel not found: {channel_url or channel_id}")
        channel = items[0]
        uploads_id = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads_id:
            raise YouTubeAPIError("Channel response did not include an uploads playlist")
        channel_title = str(channel.get("snippet", {}).get("title") or channel_config.get("name") or "")
        return str(channel["id"]), str(uploads_id), channel_title

    def latest_uploads(self, channel_config: dict, limit: int = 50) -> list[dict]:
        """Return newest public uploads with authoritative publish timestamps."""
        channel_id, uploads_id, channel_title = self._channel_lookup(channel_config)
        payload = self._get(
            "playlistItems",
            {
                "part": "snippet,contentDetails,status",
                "playlistId": uploads_id,
                "maxResults": min(max(int(limit), 1), 50),
            },
        )
        videos = []
        for item in payload.get("items", []):
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            video_id = content.get("videoId") or snippet.get("resourceId", {}).get("videoId")
            title = snippet.get("title", "")
            if not video_id or title in {"Deleted video", "Private video"}:
                continue
            videos.append({
                "video_id": str(video_id),
                "title": str(title),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published_at": str(
                    content.get("videoPublishedAt") or snippet.get("publishedAt") or ""
                ),
                "channel_id": channel_id,
                "channel_name": channel_title,
            })
        return videos
