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

# Recraft API (AI image generation for thumbnails — fallback)
RECRAFT_API_KEY = os.getenv("RECRAFT_API_KEY", "")

# fal.ai API (Nano Banana 2 — primary thumbnail generation)
FAL_KEY = os.getenv("FAL_KEY", "")

# Pipeline defaults
DEFAULT_VIDEO_MIN_LENGTH_MINUTES = 10
DEFAULT_VIDEOS_PER_MONTH = 4
THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

# Description template (reference format — actual formatting is in steps/description.py)
DESCRIPTION_TEMPLATE = """
{first_line}
{second_line}

{body}

⏱ Таймкоды:
{timestamps}

🔑 Ключевые слова: {keywords}

📌 Полезные ссылки:
{links}

{cta_text}

💬 {cta_question}

👉 Подписывайтесь на канал и нажмите 🔔, чтобы не пропустить новые видео!

{hashtags}
""".strip()

# Channel context
CHANNEL_CONTEXT_FILE = BASE_DIR / "channel_context.json"
CHANNELS_DIR = DATA_DIR / "channels"
CHANNELS_INDEX_FILE = CHANNELS_DIR / "channels.json"

def load_channel_context() -> dict:
    """Load default channel context (author, CTA, links)."""
    if CHANNEL_CONTEXT_FILE.exists():
        import json
        with open(CHANNEL_CONTEXT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_channel_context(ctx: dict):
    """Save default channel context."""
    import json
    with open(CHANNEL_CONTEXT_FILE, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)


# ── Multi-channel support ──

def list_channels() -> list[dict]:
    """List all channels from index."""
    import json
    if CHANNELS_INDEX_FILE.exists():
        with open(CHANNELS_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def _save_channels_index(channels: list[dict]):
    """Save channels index."""
    import json
    CHANNELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHANNELS_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)

def create_channel(name: str, niche: str = "", youtube_url: str = "", target_audience: str = "") -> dict:
    """Create a new channel profile."""
    import json, re
    from datetime import datetime

    # Generate channel_id from name (transliterate)
    channel_id = re.sub(r'[^a-z0-9]+', '-', name.lower().strip())
    channel_id = channel_id.strip('-')[:40] or 'channel'

    # Ensure unique
    existing = list_channels()
    existing_ids = {c["id"] for c in existing}
    base_id = channel_id
    counter = 1
    while channel_id in existing_ids:
        channel_id = f"{base_id}-{counter}"
        counter += 1

    # Create directory
    channel_dir = CHANNELS_DIR / channel_id
    channel_dir.mkdir(parents=True, exist_ok=True)

    # Create context
    context = {
        "author": {"name": "", "full_name": "", "who": "", "expertise": [], "experience": "", "tone": ""},
        "channel": {"name": name, "youtube_url": youtube_url, "telegram_url": "", "telegram_group": "", "website": "", "social_links": {}},
        "niche": niche,
        "target_audience": target_audience,
        "cta": {"subscribe": "", "like_comment": "", "lead_magnet": {"enabled": False}, "mid_roll": {"enabled": False}, "end_screen": {"enabled": True, "text": ""}},
        "description_links": [],
        "hashtags_always": [],
        "tags_always": [],
    }
    with open(channel_dir / "context.json", "w", encoding="utf-8") as f:
        json.dump(context, f, ensure_ascii=False, indent=2)

    # Add to index
    entry = {"id": channel_id, "name": name, "niche": niche, "youtube_url": youtube_url, "target_audience": target_audience, "created_at": datetime.now().isoformat()}
    existing.append(entry)
    _save_channels_index(existing)

    return entry

def load_channel_context_by_id(channel_id: str) -> dict:
    """Load channel context by ID. Falls back to default if not found."""
    import json
    if not channel_id:
        return load_channel_context()
    ctx_file = CHANNELS_DIR / channel_id / "context.json"
    if ctx_file.exists():
        with open(ctx_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return load_channel_context()

def save_channel_context_by_id(channel_id: str, ctx: dict):
    """Save channel context by ID."""
    import json
    channel_dir = CHANNELS_DIR / channel_id
    channel_dir.mkdir(parents=True, exist_ok=True)
    with open(channel_dir / "context.json", "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)

def get_channel_token_path(channel_id: str) -> Path:
    """Get OAuth token path for a channel."""
    if not channel_id:
        return YOUTUBE_TOKEN_FILE
    token_path = CHANNELS_DIR / channel_id / "token.json"
    if token_path.exists():
        return token_path
    return YOUTUBE_TOKEN_FILE  # fallback to default

# Teleprompter settings
TELEPROMPTER_FONT_SIZE = "large"
TELEPROMPTER_WORDS_PER_LINE = 6
TELEPROMPTER_PAUSE_MARKER = "⏸️"
TELEPROMPTER_EMPHASIS_MARKER = "➡️"
