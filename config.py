"""
config.py — Configuration settings for YouTube Auto-Uploader Automation.
"""

import os
import sys

# Add current folder to sys.path so credentials can be imported easily
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Load secrets from credentials.py
try:
    import credentials as _creds
    _get = lambda key, default="": getattr(_creds, key, None) or os.environ.get(key, default)
except ImportError:
    _get = lambda key, default="": os.environ.get(key, default)

BB_USERNAME = _get("BB_USERNAME")
BB_PASSWORD = _get("BB_PASSWORD")

# ── Admin site URLs ─────────────────────────────────────────────────────────────
ADMIN_BASE_URL   = "https://cms-v1.d148rwrq639wa8.amplifyapp.com"
ADMIN_LOGIN_URL  = f"{ADMIN_BASE_URL}/login"
ADMIN_UPLOAD_URL = f"{ADMIN_BASE_URL}/dashboard/cms/content-upload"

# ── Batching & Upload Limits ─────────────────────────────────────────────────────
MAX_BATCH_BYTES = 70 * 1024 * 1024  # 70 MB limit for safety (staying well under 100MB UI limit)
CUTOFF_UPLOAD_DATE = "20260801"     # Ignore videos uploaded before August 1, 2026 (YYYYMMDD)
ADMIN_CSV_HEADER = [
    "video_name", "categories_name", "age_groups", "channel_name",
    "tags", "playlist_name", "content_formats", "content_types", "language"
]

# ── Selenium Settings ───────────────────────────────────────────────────────────
SELENIUM_WAIT_SEC = 25
UPLOAD_RETRY_MAX  = 3

# ── Folder Paths ────────────────────────────────────────────────────────────────
BASE_DIR      = SCRIPT_DIR
CHANNELS_JSON = os.path.join(BASE_DIR, "channels.json")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
BATCHES_DIR   = os.path.join(BASE_DIR, "batches")
LOG_DIR       = os.path.join(BASE_DIR, "logs")
REGISTRY_DIR  = os.path.join(BASE_DIR, "registries")

# ── YouTube cookies (to bypass "Sign in to confirm you're not a bot") ───────────
# Exported cookies.txt from a logged-in YouTube session. Only used if present.
YT_COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", os.path.join(BASE_DIR, "cookies.txt"))

# Ensure required directories exist
for d in [DOWNLOADS_DIR, BATCHES_DIR, LOG_DIR, REGISTRY_DIR]:
    os.makedirs(d, exist_ok=True)
