# YouTube Channel Auto-Uploader Automation

A self-contained automation for fetching videos from YouTube channels, creating size-constrained ZIP + CSV batches with channel-specific upload properties, and automatically submitting them to the app (`admin.babybillion.in`) via Selenium.

---

## 📁 Directory Layout

```
yt_auto_uploader/
├── credentials.py       # Admin CMS credentials (DO NOT COMMIT)
├── config.py            # Global paths, limits & Selenium settings
├── channels.json        # YouTube channel configurations & CSV upload properties
├── yt_downloader.py     # yt-dlp downloader module
├── batcher.py           # ZIP archive and CSV generator with per-channel defaults
├── uploader.py          # Selenium browser driver for login and bulk upload
├── registry.py          # Per-channel deduplication registry
├── main.py              # Automation CLI runner
├── downloads/           # Downloaded MP4 files (organized by channel)
├── batches/             # Generated batch ZIPs, CSVs, and video folders
├── registries/          # JSON tracking files per channel
└── logs/                # Execution log files
```

---

## ⚙️ Configuring Channels (`channels.json`)

Add or edit YouTube channel entries in `channels.json`. Each channel specifies its channel URL and **custom upload CSV properties** (`csv_defaults`):

```json
{
  "channels": [
    {
      "id": "MyChannelID",
      "name": "My Channel Name",
      "url": "https://www.youtube.com/@MyChannelID",
      "batch_prefix": "Batch_MC",
      "max_videos_per_run": 20,
      "csv_defaults": {
        "channel_name": "MyChannelID",
        "categories_name": "Education",
        "age_groups": "3-6",
        "tags": "kids,learning",
        "playlist_name": "English Lessons",
        "content_formats": "Horizontal",
        "content_types": "Original",
        "language": "English"
      }
    }
  ]
}
```

---

## 🚀 Running the Automation

### 1. Preview Channel Videos (Dry-Run)
Check for new videos without downloading or uploading:
```bash
python yt_auto_uploader/main.py --dry-run
```

### 2. Run Automation Once for All Channels
Downloads new videos, creates CSV + ZIP batches, and uploads via Selenium in headless mode:
```bash
python yt_auto_uploader/main.py --headless
```

### 3. Continuous Watch Mode (Auto-Check Every X Seconds)
Keep the script running in the background to automatically check YouTube and upload new videos every hour (3600s):
```bash
python yt_auto_uploader/main.py --headless --watch --interval 3600
```

### 4. Run Automation for a Specific Channel
```bash
python yt_auto_uploader/main.py --channel BillionBuilders --headless
```

### 5. Download & Batch Only (Skip Upload)
```bash
python yt_auto_uploader/main.py --skip-upload
```

---

## 🐍 Local Environment Setup

YouTube's current anti-bot measures (SABR streaming restrictions, JS signature challenges) require a recent yt-dlp build, which in turn requires **Python 3.10+**. Set up a dedicated venv:

```bash
python3.10+ -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install "git+https://github.com/yt-dlp/yt-dlp.git" selenium webdriver-manager
```

`deno` must also be on `PATH` (`brew install deno`) — yt-dlp uses it to solve YouTube's JS challenges and to run the `bgutil` PO-token script provider automatically.

**No YouTube cookies are needed.** Testing showed that authenticated (cookie-based) requests trigger YouTube's PO-token/SABR restrictions and cap out around 360p, while anonymous requests from a residential IP reliably get full quality (1080p+). Cookies also expire/get invalidated quickly under repeated automated use. Just don't create a `cookies.txt` in the project root and the pipeline runs cookie-free.

---

## ⏰ Automated Daily Run (launchd)

This automation runs locally via **launchd** rather than a cloud CI service — YouTube blocks/throttles requests from shared datacenter IPs (GitHub Actions, GitLab CI, any rented VPS), so it must run from a residential IP such as this Mac.

A launchd agent is installed at `~/Library/LaunchAgents/com.babybillion.ytautouploader.plist`, running `main.py --headless` daily at **5:30 AM IST (00:00 UTC)**. Your Mac must be on and awake at that time.

```bash
# Check status
launchctl print gui/501/com.babybillion.ytautouploader

# Trigger manually (outside the schedule)
launchctl kickstart gui/501/com.babybillion.ytautouploader

# View output
tail -f logs/launchd_stdout.log logs/launchd_stderr.log

# Disable
launchctl bootout gui/501/com.babybillion.ytautouploader

# Re-enable after editing the plist
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.babybillion.ytautouploader.plist
```

To change the schedule, edit the `StartCalendarInterval` block in the plist and re-bootstrap.

---

## 🔒 Security Notice
- Credentials are stored in `credentials.py`.
- `credentials.py`, `cookies.txt`, `downloads/`, `batches/`, and `logs/` are excluded from Git via `.gitignore`.
- This repo is kept on GitHub for source control/backup only — no CI workflow runs against it, since cloud IPs get blocked by YouTube.
