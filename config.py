"""Configuration for the dashboard batch uploader."""

import os

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

try:
    import credentials as _local_credentials
except ImportError:
    _local_credentials = None

BB_USERNAME = os.environ.get("BB_USERNAME", "") or (
    getattr(_local_credentials, "BB_USERNAME", "") if _local_credentials else ""
)
BB_PASSWORD = os.environ.get("BB_PASSWORD", "") or (
    getattr(_local_credentials, "BB_PASSWORD", "") if _local_credentials else ""
)

ADMIN_BASE_URL = os.environ.get(
    "ADMIN_BASE_URL",
    "https://cms-v1.d148rwrq639wa8.amplifyapp.com",
).rstrip("/")
ADMIN_LOGIN_URL = f"{ADMIN_BASE_URL}/login"
ADMIN_UPLOAD_URL = f"{ADMIN_BASE_URL}/dashboard/cms/content-upload"

SELENIUM_WAIT_SEC = int(os.environ.get("SELENIUM_WAIT_SEC", "25"))
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", os.path.join(BASE_DIR, "downloads"))
BATCHES_DIR = os.environ.get("BATCHES_DIR", os.path.join(BASE_DIR, "batches"))
REGISTRY_DIR = os.environ.get("REGISTRY_DIR", os.path.join(BASE_DIR, "registries"))
LOG_DIR = os.environ.get(
    "UPLOAD_LOG_DIR",
    os.path.join(BASE_DIR, "logs"),
)

MAX_BATCH_BYTES = int(os.environ.get("MAX_BATCH_BYTES", str(70 * 1024 * 1024)))
DOWNLOAD_RETRIES = int(os.environ.get("DOWNLOAD_RETRIES", "3"))
MIN_PUBLISHED_DATE = os.environ.get("MIN_PUBLISHED_DATE", "2026-08-01")
YOUTUBE_API_KEYS = [
    key.strip()
    for key in os.environ.get(
        "YOUTUBE_API_KEYS",
        os.environ.get("YOUTUBE_API_KEY", ""),
    ).split(",")
    if key.strip()
]
YT_COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", "")
GOOGLE_SHEETS_REGISTRY_ID = os.environ.get(
    "GOOGLE_SHEETS_REGISTRY_ID",
    "1ONiUJ9LVqO0Sw8IFMQ4n7ubvADgiVI08QHIZrGcdNho",
).strip()
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
).strip()
ADMIN_CSV_HEADER = [
    "video_name",
    "categories_name",
    "age_groups",
    "channel_name",
    "tags",
    "playlist_name",
    "content_formats",
    "content_types",
    "language",
]

for directory in (DOWNLOADS_DIR, BATCHES_DIR, REGISTRY_DIR, LOG_DIR):
    os.makedirs(directory, exist_ok=True)
