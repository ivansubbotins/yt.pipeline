"""YouTube Data API v3 wrapper for video upload, metadata, and scheduling."""

import json
import logging
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config import (
    YOUTUBE_CLIENT_ID,
    YOUTUBE_CLIENT_SECRET,
    YOUTUBE_REDIRECT_URI,
    YOUTUBE_TOKEN_FILE,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class YouTubeAPI:
    """Wrapper around YouTube Data API v3."""

    def __init__(self):
        self._service = None
        self._credentials = None

    def authenticate(self):
        """Authenticate with OAuth2. Opens browser on first run."""
        creds = None

        if YOUTUBE_TOKEN_FILE.exists():
            with open(YOUTUBE_TOKEN_FILE) as f:
                token_data = json.load(f)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not creds or not creds.valid:
            client_config = {
                "installed": {
                    "client_id": YOUTUBE_CLIENT_ID,
                    "client_secret": YOUTUBE_CLIENT_SECRET,
                    "redirect_uris": [YOUTUBE_REDIRECT_URI],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=8080)

        # Save token
        with open(YOUTUBE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

        self._credentials = creds
        self._service = build("youtube", "v3", credentials=creds)
        logger.info("YouTube API authenticated successfully")

    @property
    def service(self):
        if not self._service:
            self.authenticate()
        return self._service

    def upload_video(
        self,
        file_path: str,
        title: str,
        description: str,
        tags: list[str],
        category_id: str = "22",  # People & Blogs
        privacy: str = "private",
        publish_at: str | None = None,
        thumbnail_path: str | None = None,
    ) -> dict:
        """
        Upload a video to YouTube.

        Args:
            file_path: Path to the video file.
            title: Video title.
            description: Video description.
            tags: List of tags.
            category_id: YouTube category ID.
            privacy: 'private', 'unlisted', or 'public'.
            publish_at: ISO 8601 datetime for scheduled publishing.
            thumbnail_path: Path to thumbnail image (1280x720).

        Returns:
            dict with video id and URL.
        """
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        if publish_at and privacy == "private":
            body["status"]["publishAt"] = publish_at

        media = MediaFileUpload(file_path, chunksize=10 * 1024 * 1024, resumable=True)

        logger.info(f"Uploading video: {title}")
        request = self.service.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response["id"]
        logger.info(f"Video uploaded: {video_id}")

        # Set thumbnail if provided
        if thumbnail_path and Path(thumbnail_path).exists():
            self.set_thumbnail(video_id, thumbnail_path)

        return {"video_id": video_id, "url": f"https://youtu.be/{video_id}"}

    def set_thumbnail(self, video_id: str, thumbnail_path: str):
        """Set custom thumbnail for a video."""
        media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        self.service.thumbnails().set(videoId=video_id, media_body=media).execute()
        logger.info(f"Thumbnail set for video {video_id}")

    def update_video(self, video_id: str, title: str | None = None, description: str | None = None, tags: list[str] | None = None) -> dict:
        """Update video metadata."""
        video = self.service.videos().list(part="snippet", id=video_id).execute()
        if not video["items"]:
            raise ValueError(f"Video {video_id} not found")

        snippet = video["items"][0]["snippet"]
        if title:
            snippet["title"] = title
        if description:
            snippet["description"] = description
        if tags:
            snippet["tags"] = tags

        result = self.service.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
        logger.info(f"Video {video_id} updated")
        return result

    def set_publish_schedule(self, video_id: str, publish_at: str):
        """Schedule a private video for future publication."""
        body = {
            "id": video_id,
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_at,
            },
        }
        self.service.videos().update(part="status", body=body).execute()
        logger.info(f"Video {video_id} scheduled for {publish_at}")

    def make_public(self, video_id: str):
        """Make a video public immediately."""
        body = {
            "id": video_id,
            "status": {"privacyStatus": "public"},
        }
        self.service.videos().update(part="status", body=body).execute()
        logger.info(f"Video {video_id} set to public")

    def get_channel_info(self) -> dict:
        """Get authenticated user's channel info."""
        result = self.service.channels().list(part="snippet,statistics", mine=True).execute()
        if result["items"]:
            channel = result["items"][0]
            return {
                "id": channel["id"],
                "title": channel["snippet"]["title"],
                "subscribers": channel["statistics"].get("subscriberCount"),
                "videos": channel["statistics"].get("videoCount"),
                "url": f"https://www.youtube.com/channel/{channel['id']}",
            }
        return {}

    def search_videos(self, query: str, max_results: int = 10, order: str = "relevance") -> list[dict]:
        """Search YouTube videos for research purposes."""
        result = self.service.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=max_results,
            order=order,
        ).execute()

        videos = []
        for item in result.get("items", []):
            videos.append({
                "video_id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "published_at": item["snippet"]["publishedAt"],
                "description": item["snippet"]["description"],
                "url": f"https://youtu.be/{item['id']['videoId']}",
            })
        return videos

    def get_video_stats(self, video_id: str) -> dict:
        """Get video statistics (views, likes, comments)."""
        result = self.service.videos().list(part="statistics,snippet", id=video_id).execute()
        if result["items"]:
            item = result["items"][0]
            stats = item["statistics"]
            return {
                "title": item["snippet"]["title"],
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            }
        return {}
