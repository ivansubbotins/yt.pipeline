"""YouTube Pipeline configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "templates"

# YouTube API
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", "http://localhost:8080")
YOUTUBE_TOKEN_FILE = BASE_DIR / "youtube_token.json"

# Anthropic (для генерации контента)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# Recraft API (AI image generation for thumbnails)
RECRAFT_API_KEY = os.getenv("RECRAFT_API_KEY", "")

# Pipeline defaults
DEFAULT_VIDEO_MIN_LENGTH_MINUTES = 10
DEFAULT_VIDEOS_PER_MONTH = 4
THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

# Description template
DESCRIPTION_TEMPLATE = """
{title}

{summary}

⏱ Таймкоды:
{timestamps}

🔑 Ключевые слова: {keywords}

📌 Полезные ссылки:
{links}

👉 Подписывайтесь на канал: {channel_url}
🔔 Нажмите колокольчик, чтобы не пропустить новые видео!

#shorts #{hashtags}
""".strip()

# Teleprompter settings
TELEPROMPTER_FONT_SIZE = "large"
TELEPROMPTER_WORDS_PER_LINE = 6
TELEPROMPTER_PAUSE_MARKER = "⏸️"
TELEPROMPTER_EMPHASIS_MARKER = "➡️"
