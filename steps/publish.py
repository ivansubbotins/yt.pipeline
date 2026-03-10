"""Step 10: Publish — upload video to YouTube after Ivan's approval."""

import logging

from steps.base import BaseStep
from youtube_api import YouTubeAPI

logger = logging.getLogger(__name__)


class PublishStep(BaseStep):
    step_name = "publish"

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        description_data = self.get_previous_step_data("description")

        title = description_data.get("title", content_plan.get("title", self.state.topic))
        description = description_data.get("description", "")
        tags = description_data.get("tags", content_plan.get("tags", []))

        # Check for video file
        video_file = self.state.get_step("editing").get("data", {}).get("video_file")
        if not video_file:
            # Look in project dir for common video formats
            for ext in ["mp4", "mov", "mkv", "avi"]:
                candidates = list(self.state.project_dir.glob(f"*.{ext}"))
                if candidates:
                    video_file = str(candidates[0])
                    break

        if not video_file:
            raise FileNotFoundError(
                f"Video file not found in {self.state.project_dir}. "
                "Place the video file in the project directory or set it in editing step data."
            )

        # Check for thumbnail
        thumbnail_file = None
        for ext in ["jpg", "jpeg", "png"]:
            candidates = list(self.state.project_dir.glob(f"thumbnail*.{ext}"))
            if candidates:
                thumbnail_file = str(candidates[0])
                break

        # Upload
        yt = YouTubeAPI()
        result = yt.upload_video(
            file_path=video_file,
            title=title,
            description=description,
            tags=tags,
            privacy="private",  # Always private first — Ivan makes public manually or via schedule
            thumbnail_path=thumbnail_file,
        )

        logger.info(f"Video published (private): {result['url']}")
        return result
