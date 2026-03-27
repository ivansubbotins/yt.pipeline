"""Base class for pipeline steps."""

import logging
from abc import ABC, abstractmethod

import anthropic

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, load_channel_context
from state import PipelineState, StepStatus

logger = logging.getLogger(__name__)


class BaseStep(ABC):
    """Base class for all pipeline steps."""

    step_name: str = ""

    def __init__(self, state: PipelineState):
        self.state = state
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = ANTHROPIC_MODEL

    def run(self) -> dict:
        """Execute the step with status tracking."""
        logger.info(f"[{self.state.project_id}] Starting step: {self.step_name}")
        self.state.set_step_status(self.step_name, StepStatus.IN_PROGRESS)

        try:
            result = self.execute()
            self.state.set_step_data(self.step_name, result)
            self.state.set_step_status(self.step_name, StepStatus.COMPLETED)
            logger.info(f"[{self.state.project_id}] Completed step: {self.step_name}")
            return result
        except Exception as e:
            logger.error(f"[{self.state.project_id}] Failed step {self.step_name}: {e}")
            self.state.set_step_status(self.step_name, StepStatus.FAILED)
            raise

    @abstractmethod
    def execute(self) -> dict:
        """Implement the actual step logic. Returns result data dict."""
        ...

    def get_channel_context(self) -> dict:
        """Load channel context (cached per step instance)."""
        if not hasattr(self, "_channel_ctx"):
            self._channel_ctx = load_channel_context()
        return self._channel_ctx

    def _build_author_context(self) -> str:
        """Build author/channel context string for system prompt."""
        ctx = self.get_channel_context()
        if not ctx:
            return ""

        parts = []
        author = ctx.get("author", {})
        if author.get("name"):
            parts.append(f"Автор канала: {author.get('full_name', author['name'])}")
        if author.get("who"):
            parts.append(f"Кто он: {author['who']}")
        if author.get("expertise"):
            parts.append(f"Экспертиза: {', '.join(author['expertise'])}")
        if author.get("experience"):
            parts.append(f"Опыт: {author['experience']}")
        if author.get("tone"):
            parts.append(f"Тон подачи: {author['tone']}")

        channel = ctx.get("channel", {})
        if channel.get("name"):
            parts.append(f"Название канала: {channel['name']}")

        cta = ctx.get("cta", {})
        lead = cta.get("lead_magnet", {})
        if lead.get("enabled") and lead.get("text"):
            parts.append(f"Лидмагнит ({lead.get('placement', 'intro')}): {lead['text']}")
        mid = cta.get("mid_roll", {})
        if mid.get("enabled") and mid.get("text"):
            parts.append(f"Рекламная вставка (после блока {mid.get('placement_after_block', 3)}): {mid['text']}")

        if not parts:
            return ""
        return "Контекст автора и канала:\n" + "\n".join(parts)

    def ask_claude(self, system: str, prompt: str) -> str:
        """Send a prompt to Claude and return the response text."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        author_ctx = self._build_author_context()
        system = "Сегодняшняя дата: " + today + ". Контекст: Россия, русскоязычная аудитория, все цены в рублях, российские реалии. Используй актуальный год.\n\n" + (author_ctx + "\n\n" if author_ctx else "") + system
        result = ""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=32000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                result += text
        return result

    def get_previous_step_data(self, step_name: str) -> dict:
        """Get data from a previously completed step."""
        return self.state.get_step(step_name).get("data", {})
