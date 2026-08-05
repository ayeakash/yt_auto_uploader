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

## ⏰ Automated Cron Schedule (Optional)

To run automatically in the background on macOS/Linux using `crontab` (e.g., every 6 hours):

1. Open crontab: `crontab -e`
2. Add the following entry (adjust paths to your environment):
```cron
0 */6 * * * cd /Users/akashmarkad/Akash/Code/yt_auto_uploader && /usr/bin/python3 main.py --headless >> logs/cron.log 2>&1
```

---

## ⚡ Running in GitHub Actions (Recommended)

A pre-configured GitHub Actions workflow is provided in [`.github/workflows/auto_uploader.yml`](file:///Users/akashmarkad/Akash/Code/yt_auto_uploader/.github/workflows/auto_uploader.yml). It automatically runs **once every day at 00:00 UTC** and can also be triggered manually from the GitHub Actions tab.

### Setup Steps:

1. **Push this repository to GitHub**.
2. **Add Repository Secrets**:
   - Go to **Settings > Secrets and variables > Actions > New repository secret**.
   - Add **`BB_USERNAME`**: Your CMS login username/email.
   - Add **`BB_PASSWORD`**: Your CMS login password.
3. **Enable Workflow Permissions** (for saving deduplication state):
   - Go to **Settings > Actions > General > Workflow permissions**.
   - Select **Read and write permissions** (so the action can commit updated tracking files in `registries/`).

Now, GitHub Actions will automatically monitor your configured YouTube channels, download new uploads, package them into batches, and post them directly to `admin.babybillion.in` completely hands-free!

---

## 🔒 Security Notice
- Credentials are standardly stored in `credentials.py`.
- `credentials.py`, `downloads/`, `batches/`, and `registries/` are excluded from Git via `.gitignore`.
