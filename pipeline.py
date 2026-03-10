"""Pipeline orchestrator — runs steps in sequence, handles manual stages."""

import json
import logging
from datetime import datetime

from state import PipelineState, StepStatus, PIPELINE_STEPS
from steps.research import ResearchStep
from steps.content_plan import ContentPlanStep
from steps.references import ReferencesStep
from steps.script import ScriptStep
from steps.teleprompter import TeleprompterStep
from steps.covers import CoversStep
from steps.description import DescriptionStep
from steps.publish import PublishStep

logger = logging.getLogger(__name__)

# Map step names to their implementations
STEP_CLASSES = {
    "research": ResearchStep,
    "content_plan": ContentPlanStep,
    "references": ReferencesStep,
    "script": ScriptStep,
    "teleprompter": TeleprompterStep,
    "covers": CoversStep,
    "description": DescriptionStep,
    "publish": PublishStep,
}

# Steps that require human action
MANUAL_STEPS = {"shooting", "editing"}


class Pipeline:
    """Orchestrates the YouTube video production pipeline."""

    def __init__(self, project_id: str, topic: str | None = None):
        self.state = PipelineState(project_id)
        if topic:
            self.state.topic = topic

    def run_step(self, step_name: str) -> dict:
        """Run a single step by name."""
        if step_name in MANUAL_STEPS:
            logger.info(f"Step '{step_name}' is manual — marking as waiting")
            self.state.mark_waiting(step_name)
            return {"status": "waiting", "message": f"Ожидание ручного этапа: {step_name}"}

        step_cls = STEP_CLASSES.get(step_name)
        if not step_cls:
            raise ValueError(f"Unknown step: {step_name}")

        step = step_cls(self.state)
        return step.run()

    def run_auto_steps(self) -> dict:
        """Run all automated steps (1-7) sequentially.

        Stops at manual steps (shooting, editing) and before publish.
        Returns summary of executed steps.
        """
        results = {}
        auto_steps = ["research", "content_plan", "references", "script", "teleprompter", "covers", "description"]

        for step_name in auto_steps:
            step_info = self.state.get_step(step_name)
            if step_info["status"] in (StepStatus.COMPLETED, StepStatus.APPROVED):
                logger.info(f"Skipping already completed step: {step_name}")
                continue

            logger.info(f"Running step: {step_name}")
            result = self.run_step(step_name)
            results[step_name] = result

            # Advance state
            if self.state.current_step == step_name:
                self.state.advance()

        # Mark shooting as waiting
        self.state.mark_waiting("shooting")

        return results

    def resume_after_shooting(self):
        """Mark shooting as done, advance to editing."""
        self.state.set_step_status("shooting", StepStatus.COMPLETED)
        self.state._state["current_step"] = "editing"
        self.state.mark_waiting("editing")
        self.state._save()
        logger.info("Shooting marked as done. Waiting for editing.")

    def resume_after_editing(self, video_file: str | None = None):
        """Mark editing as done. Optionally set video file path."""
        self.state.set_step_status("editing", StepStatus.COMPLETED)
        if video_file:
            self.state.update_step_data("editing", "video_file", video_file)
        self.state._state["current_step"] = "publish"
        self.state._save()
        logger.info("Editing marked as done. Ready for publication approval.")

    def publish(self, approved: bool = False) -> dict:
        """Publish video (only if approved by Ivan)."""
        if not approved:
            return {"status": "blocked", "message": "Публикация требует утверждения Иваном. Используйте --approve."}

        self.state.mark_approved("publish")
        result = self.run_step("publish")
        self.state.advance()
        return result

    def review(self) -> str:
        """Generate a review summary of all completed steps for Ivan."""
        lines = [
            f"{'=' * 60}",
            f"РЕВЬЮ ПРОЕКТА: {self.state.project_id}",
            f"Тема: {self.state.topic}",
            f"{'=' * 60}",
            "",
        ]

        # Content plan
        cp = self.state.get_step("content_plan").get("data", {})
        if cp:
            lines.append(f"📋 ЗАГОЛОВОК: {cp.get('title', '—')}")
            lines.append(f"⏱  ХРОНОМЕТРАЖ: ~{cp.get('target_length_minutes', '?')} мин")
            lines.append(f"🎯 ХУК: {cp.get('hook', '—')}")
            lines.append("")

        # Script summary
        script = self.state.get_step("script").get("data", {})
        if script:
            blocks = script.get("blocks", script.get("scenes", []))
            lines.append(f"🎬 СЦЕНАРИЙ: {len(blocks)} блоков, ~{script.get('total_duration_minutes', '?')} мин")
            for b in blocks:
                num = b.get("block_number", b.get("scene_number", "?"))
                name = b.get("name", "—")
                ts = b.get("timestamp_start", "")
                dur = b.get("duration_seconds", 0)
                btype = b.get("block_type", b.get("type", ""))
                lines.append(f"   {num}. [{ts}] {name} ({dur}s) [{btype}]")
            if script.get("climax_setup"):
                lines.append(f"   🎯 Кульминация: {script['climax_setup']}")
            lines.append("")

        # Teleprompter
        tp = self.state.get_step("teleprompter").get("data", {})
        if tp:
            lines.append(f"📄 СУФЛЁР: {tp.get('total_word_count', '?')} слов, ~{tp.get('estimated_read_time_minutes', '?')} мин")
            if tp.get("teleprompter_file"):
                lines.append(f"   Файл: {tp['teleprompter_file']}")
            lines.append("")

        # Description
        desc = self.state.get_step("description").get("data", {})
        if desc:
            lines.append(f"📝 DESCRIPTION: готово")
            lines.append(f"   Теги: {', '.join(desc.get('tags', []))}")
            if desc.get("description_file"):
                lines.append(f"   Файл: {desc['description_file']}")
            lines.append("")

        # Thumbnails
        covers = self.state.get_step("covers").get("data", {})
        if covers:
            thumbs = covers.get("thumbnails", [])
            lines.append(f"🎨 ОБЛОЖКИ: {len(thumbs)} вариантов")
            for t in thumbs:
                lines.append(f"   - {t.get('name', '?')}: \"{t.get('text_overlay', '')}\"")
            lines.append("")

        # Status
        lines.append(self.state.summary())

        return "\n".join(lines)

    def export_for_review(self) -> str:
        """Export all generated content to a review file."""
        review_text = self.review()
        review_file = self.state.project_dir / "REVIEW.txt"
        with open(review_file, "w") as f:
            f.write(review_text)
        logger.info(f"Review exported to {review_file}")
        return str(review_file)
