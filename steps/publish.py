"""Step 10: Publish — upload video to YouTube after Ivan's approval."""

import logging
from pathlib import Path

from steps.base import BaseStep
from youtube_api import YouTubeAPI

logger = logging.getLogger(__name__)

# YouTube category IDs for common categories
CATEGORY_MAP = {
    "education": "27",
    "science": "28",
    "tech": "28",
    "technology": "28",
    "howto": "26",
    "entertainment": "24",
    "gaming": "20",
    "news": "25",
    "people": "22",
    "comedy": "23",
    "film": "1",
    "music": "10",
    "sports": "17",
    "travel": "19",
    "autos": "2",
    "pets": "15",
    "nonprofit": "29",
}

DEFAULT_CATEGORY_ID = "22"  # People & Blogs


class PublishStep(BaseStep):
    step_name = "publish"

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        description_data = self.get_previous_step_data("description")
        research_data = self.get_previous_step_data("research")

        # --- Title ---
        title = description_data.get("title", content_plan.get("title", self.state.topic))

        # --- Description ---
        description = description_data.get("description", "")
        # Try loading from file if description text is empty
        if not description:
            desc_file = self.state.project_dir / "description.txt"
            if desc_file.exists():
                description = desc_file.read_text(encoding="utf-8")

        # --- Tags ---
        tags = description_data.get("tags", [])
        if not tags:
            tags = content_plan.get("tags", [])
        if not tags and research_data:
            tags = research_data.get("keywords", [])

        # --- Category ---
        category_id = self._resolve_category(content_plan, research_data)

        # --- Video file ---
        video_file = self._find_video_file()

        # --- Thumbnail ---
        thumbnail_file = self._find_thumbnail()

        # --- Schedule ---
        publish_at = self.state.get_step("publish").get("data", {}).get("publish_at")
        privacy = "private"

        # --- Playlist ---
        playlist_id = self.state.get_step("publish").get("data", {}).get("playlist_id")

        # Upload
        yt = YouTubeAPI()

        logger.info(f"Uploading: title='{title}', tags={len(tags)}, "
                     f"category={category_id}, schedule={publish_at}")

        result = yt.upload_video(
            file_path=video_file,
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy=privacy,
            publish_at=publish_at,
            thumbnail_path=thumbnail_file,
        )

        video_id = result["video_id"]

        # Add to playlist if specified
        if playlist_id:
            try:
                yt.add_to_playlist(playlist_id, video_id)
                result["playlist_id"] = playlist_id
                logger.info(f"Added to playlist: {playlist_id}")
            except Exception as e:
                logger.warning(f"Failed to add to playlist {playlist_id}: {e}")
                result["playlist_error"] = str(e)

        # Store full metadata in result
        result["title"] = title
        result["tags"] = tags
        result["category_id"] = category_id
        result["has_thumbnail"] = thumbnail_file is not None
        result["has_description"] = bool(description)
        if publish_at:
            result["publish_at"] = publish_at

        logger.info(f"Video uploaded (private): {result['url']}")
        if publish_at:
            logger.info(f"Scheduled for publication at: {publish_at}")

        return result

    def _resolve_category(self, content_plan: dict, research_data: dict) -> str:
        """Determine the best YouTube category ID from pipeline data."""
        # Check if explicitly set in publish step data
        explicit = self.state.get_step("publish").get("data", {}).get("category_id")
        if explicit:
            return explicit

        # Try to match from content plan or research keywords
        category_hint = content_plan.get("category", "")
        if category_hint:
            cat_lower = category_hint.lower()
            for key, cat_id in CATEGORY_MAP.items():
                if key in cat_lower:
                    return cat_id

        # Try matching from research topic/keywords
        if research_data:
            topic_lower = research_data.get("topic", "").lower()
            keywords = [k.lower() for k in research_data.get("keywords", [])]
            all_text = topic_lower + " " + " ".join(keywords)
            for key, cat_id in CATEGORY_MAP.items():
                if key in all_text:
                    return cat_id

        return DEFAULT_CATEGORY_ID

    def _find_video_file(self) -> str:
        """Locate the video file to upload."""
        # First check editing step data
        video_file = self.state.get_step("editing").get("data", {}).get("video_file")
        if video_file and Path(video_file).exists():
            return video_file

        # Search project directory
        for ext in ("mp4", "mov", "mkv", "avi", "webm"):
            candidates = list(self.state.project_dir.glob(f"*.{ext}"))
            if candidates:
                # Prefer the most recently modified file
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return str(candidates[0])

        raise FileNotFoundError(
            f"Video file not found in {self.state.project_dir}. "
            "Place the video file in the project directory or set it via: "
            "python agent.py edit-done <project_id> <path/to/video.mp4>"
        )

    def _find_thumbnail(self) -> str | None:
        """Locate the best thumbnail file."""
        # Prefer thumbnail.jpg/png (the covers step copies the best one there)
        for name in ("thumbnail.jpg", "thumbnail.jpeg", "thumbnail.png"):
            path = self.state.project_dir / name
            if path.exists():
                return str(path)

        # Fall back to thumbnails/ directory
        thumbs_dir = self.state.project_dir / "thumbnails"
        if thumbs_dir.exists():
            for ext in ("jpg", "jpeg", "png"):
                candidates = list(thumbs_dir.glob(f"*.{ext}"))
                if candidates:
                    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    return str(candidates[0])

        return None
