"""Base class for pipeline steps."""

import logging
from abc import ABC, abstractmethod

import anthropic

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, load_channel_context
from state import PipelineState, StepStatus

logger = logging.getLogger(__name__)

# Claude pricing (per 1M tokens) — Sonnet 4 / Sonnet 3.5
CLAUDE_PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0},
    "claude-3-5-sonnet-latest": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-3-opus-20240229": {"input": 15.0, "output": 75.0},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}


class BaseStep(ABC):
    """Base class for all pipeline steps."""

    step_name: str = ""

    def __init__(self, state: PipelineState):
        self.state = state
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = ANTHROPIC_MODEL
        self._usage_total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}

    def run(self) -> dict:
        """Execute the step with status tracking."""
        logger.info(f"[{self.state.project_id}] Starting step: {self.step_name}")
        self.state.set_step_status(self.step_name, StepStatus.IN_PROGRESS)
        self._usage_total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}

        try:
            result = self.execute()
            # Inject usage data into result
            if isinstance(result, dict):
                result["_usage"] = self._usage_total.copy()
            self.state.set_step_data(self.step_name, result)
            self.state.set_step_status(self.step_name, StepStatus.COMPLETED)
            usage = self._usage_total
            logger.info(
                f"[{self.state.project_id}] Completed step: {self.step_name} | "
                f"Tokens: {usage['input_tokens']:,} in / {usage['output_tokens']:,} out | "
                f"Cost: ${usage['cost_usd']:.4f} ({usage['calls']} calls)"
            )
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
        """Load channel context (cached per step instance). Uses per-channel context if project has channel_id."""
        if not hasattr(self, "_channel_ctx"):
            channel_id = self.state.channel_id
            if channel_id:
                from config import load_channel_context_by_id
                self._channel_ctx = load_channel_context_by_id(channel_id)
            else:
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
        """Send a prompt to Claude and return the response text. Tracks token usage."""
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
            # Get final message with usage stats
            final = stream.get_final_message()

        # Track usage
        if final and hasattr(final, 'usage') and final.usage:
            inp = final.usage.input_tokens or 0
            out = final.usage.output_tokens or 0
            pricing = CLAUDE_PRICING.get(self.model, {"input": 3.0, "output": 15.0})
            cost = (inp * pricing["input"] + out * pricing["output"]) / 1_000_000
            self._usage_total["input_tokens"] += inp
            self._usage_total["output_tokens"] += out
            self._usage_total["cost_usd"] += cost
            self._usage_total["calls"] += 1
            logger.info(f"  Claude call: {inp:,} in + {out:,} out = ${cost:.4f}")

        return result

    def get_previous_step_data(self, step_name: str) -> dict:
        """Get data from a previously completed step."""
        return self.state.get_step(step_name).get("data", {})
