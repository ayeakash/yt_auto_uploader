# Daily YouTube → App Video Sync

An idempotent daily pipeline that discovers new uploads from configured YouTube
channels, downloads and validates the media, builds channel-specific CSV/ZIP
batches, and submits them through the CMS dashboard.

Only download and republish videos you own or are authorized to use.

## Reliability design

1. The YouTube Data API discovers uploads and authoritative publication dates.
   It uses `channels.list` to resolve a channel's uploads playlist and
   `playlistItems.list` to read up to 50 recent uploads—normally two low-cost API
   calls per channel per run.
2. If the API has no configured key or is temporarily unavailable, `yt-dlp`
   performs channel discovery as a fallback.
3. `yt-dlp` is the primary media downloader. It retries requests and fragments,
   resumes partial files, fetches parallel fragments, and merges the best MP4
   video/audio streams with FFmpeg.
4. `pytubefix` is the independent fallback. It downloads adaptive MP4 video and
   audio streams and merges them with FFmpeg.
5. FFprobe rejects missing, truncated, video-only, or audio-only results before
   a download is recorded as successful.
6. A shared Google Sheet tracks `downloaded → batched → uploaded` across
   computers. Failed downloads remain retryable, and failed dashboard batches
   are reconstructed and retried on the next run. Local JSON files are retained
   only as a cache/fallback when Sheets is not configured.
7. A process lock prevents overlapping scheduled runs and duplicate work.

## Setup

Python 3.10+, Chrome, and FFmpeg/FFprobe are required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put your API key(s) and dashboard credentials in `.env`. This file is ignored by
Git. A comma-separated key pool is supported:

```dotenv
YOUTUBE_API_KEYS=first_key,second_key
BB_USERNAME=dashboard@example.com
BB_PASSWORD=replace_me
GOOGLE_SHEETS_REGISTRY_ID=1ONiUJ9LVqO0Sw8IFMQ4n7ubvADgiVI08QHIZrGcdNho
GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/google-service-account.json
```

For shared deduplication, create a Google Cloud service account, enable the
Google Sheets API, download its JSON key, and share the registry Sheet with the
key's `client_email` as an editor. Put the JSON key outside the repository and
set `GOOGLE_SERVICE_ACCOUNT_FILE` on every computer. Once configured, a Sheets
read or write failure stops the run instead of falling back to stale local data,
which prevents accidental duplicate uploads.

Shared registry: https://docs.google.com/spreadsheets/d/1ONiUJ9LVqO0Sw8IFMQ4n7ubvADgiVI08QHIZrGcdNho/edit

## Channel configuration

`channels.json` only needs the channel URL and its language:

```json
{
  "channels": [
    {
      "id": "example",
      "name": "Example Channel",
      "url": "https://www.youtube.com/@ExampleHandle",
      "language": "English",
      "batch_prefix": "Batch_EX",
      "discovery_limit": 50,
      "max_videos_per_run": 20
    }
  ]
}
```

The URL may use an `@handle` or `/channel/UC...` form. An explicit `channel_id`
field is also supported. The configured `name` is used in CSV output; YouTube's
resolved channel title is the fallback when `name` is omitted.

Every generated CSV uses these defaults:

- `channel_name`: configured channel name (resolved YouTube title as fallback)
- `categories_name`: `Entertainment`
- `age_groups`: empty
- `tags`: empty
- `playlist_name`: empty
- `content_formats`: empty
- `content_types`: `Original`
- `language`: configured per channel

CSV text is ASCII, web-friendly, and underscore-separated. Video names use
`Title_Case_With_Underscores__YouTubeVideoID` without an extension; the
corresponding ZIP entry uses the exact same stem plus `.mp4`. For example:

```text
CSV video_name: Kids_Cook_Giant_Veggie_Toast__xZGEkXrhk-I
ZIP filename:   Kids_Cook_Giant_Veggie_Toast__xZGEkXrhk-I.mp4
channel_name:   Billion_Builders
```

An optional `csv_defaults` object can override a field for an exceptional channel.

## Run

Preview new candidates without downloading:

```bash
.venv/bin/python pipeline.py --dry-run
```

Download and batch without uploading:

```bash
.venv/bin/python pipeline.py --skip-upload
```

Run the complete pipeline:

```bash
.venv/bin/python pipeline.py --headless
```

Useful recovery commands:

```bash
# Retry pending batches without checking YouTube
.venv/bin/python pipeline.py --skip-download --headless

# Run one channel only
.venv/bin/python pipeline.py --channel example --headless
```

Exit status is nonzero if any channel or upload fails, making the command suitable
for `launchd`, cron, or another scheduler. Logs rotate in `logs/pipeline.log`.

## Daily scheduling

On this Mac, `launchd` is preferable to cloud CI because YouTube media extraction
is more reliable from the same residential network used for manual verification.
The installed LaunchAgent runs this command every day at **5:30 AM local time**:

```bash
/absolute/path/to/yt_auto_uploader/.venv/bin/python \
  /absolute/path/to/yt_auto_uploader/pipeline.py --headless
```

The pipeline ignores and permanently records as out of scope every video
published before **August 1, 2026**. Override the boundary only when intentional:

```dotenv
MIN_PUBLISHED_DATE=2026-08-01
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```
