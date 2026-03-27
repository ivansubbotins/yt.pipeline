"""Step 5: Teleprompter text — convert script skeleton to full teleprompter-ready text.

Generates word-for-word text the host reads from the teleprompter,
with pauses, accents, emotion cues, and large-font-friendly formatting.
"""

import json
import logging
import textwrap

from steps.base import BaseStep
from config import (
    TELEPROMPTER_WORDS_PER_LINE,
    TELEPROMPTER_PAUSE_MARKER,
    TELEPROMPTER_EMPHASIS_MARKER,
)

logger = logging.getLogger(__name__)

# Markers used in the formatted .txt output
PAUSE_SHORT = "⏸️"          # ~1 second
PAUSE_LONG = "⏸️⏸️"        # ~2 seconds
PAUSE_BREATH = "⏸️⏸️⏸️"   # ~3 seconds (between blocks)
EMPHASIS = "➡️"
EMOTION_OPEN = "["
EMOTION_CLOSE = "]"
DIRECTION_OPEN = "{"
DIRECTION_CLOSE = "}"

SYSTEM_PROMPT = """Ты — профессиональный спичрайтер для YouTube.

Твоя задача: превратить скелет сценария в ПОЛНОЦЕННЫЙ дословный текст для суфлёра.

КРИТИЧЕСКИ ВАЖНО — в тексте для суфлёра:
- НИКАКИХ спецсимволов, эмодзи, смайликов
- НИКАКИХ пояснений в скобках типа [серьёзно], {жест}, (пауза)
- НИКАКИХ технических пометок, обозначений, маркеров
- Только ЧИСТЫЙ ТЕКСТ — слова, которые ведущий произносит вслух
- Между смысловыми блоками — пустая строка (это и есть пауза)

Правила написания:
1. Короткие предложения — максимум 12-15 слов
2. Разговорный стиль — как будто говоришь с другом
3. Избегай канцелярита и сложных оборотов
4. Риторические вопросы для вовлечения зрителя
5. Текст должен звучать ЕСТЕСТВЕННО при чтении вслух
6. Минимум 2000 слов для 12+ минут видео (темп ~160 слов/мин)
7. Контекст: Россия, цены в рублях, российские реалии

Формат JSON-ответа (без markdown-блоков):
{
  "scenes": [
    {
      "scene_name": "название сцены",
      "block_type": "hook | intro | main | climax | cta_outro",
      "timestamp": "0:00 — 0:30",
      "emotion_tone": "основная эмоция блока",
      "teleprompter_text": "полный чистый текст блока БЕЗ спецсимволов"
    }
  ],
  "total_word_count": 2000,
  "estimated_read_time_minutes": 12,
  "pacing_summary": "краткое описание ритма",
  "full_text": "ВЕСЬ чистый текст для суфлёра целиком"
}"""


def _format_teleprompter_txt(result: dict, words_per_line: int) -> str:
    """Format the full teleprompter text for large-font reading.

    Takes the JSON result and produces a clean .txt file optimised
    for teleprompter apps: short lines, clear block separators,
    generous spacing.
    """
    lines: list[str] = []

    # Header
    lines.append("=" * 40)
    lines.append("ТЕКСТ ДЛЯ СУФЛЁРА")
    lines.append("=" * 40)
    lines.append("")
    lines.append(f"Слов: ~{result.get('total_word_count', '?')}")
    lines.append(f"Время чтения: ~{result.get('estimated_read_time_minutes', '?')} мин")
    lines.append("")
    lines.append("Обозначения:")
    lines.append(f"  {PAUSE_SHORT} — пауза 1 сек")
    lines.append(f"  {PAUSE_LONG} — пауза 2 сек")
    lines.append(f"  {PAUSE_BREATH} — пауза 3 сек")
    lines.append(f"  {EMPHASIS} СЛОВО {EMPHASIS} — акцент голосом")
    lines.append("  [эмоция] — подсказка для тона")
    lines.append("  {действие} — визуальное указание")
    lines.append("  КАПСЛОК — произнести громче")
    lines.append("")
    lines.append("=" * 40)
    lines.append("")
    lines.append("")

    scenes = result.get("scenes", [])
    for i, scene in enumerate(scenes):
        # Block header
        block_num = i + 1
        block_type = scene.get("block_type", "").upper()
        scene_name = scene.get("scene_name", f"Блок {block_num}")
        timestamp = scene.get("timestamp", "")

        lines.append("━" * 40)
        lines.append(f"  === БЛОК {block_num}: {scene_name.upper()} ===")
        if block_type:
            lines.append(f"  [{block_type}]")
        if timestamp:
            lines.append(f"  [хронометраж: {timestamp}]")
        emotion = scene.get("emotion_tone", "")
        if emotion:
            lines.append(f"  [тон: {emotion}]")
        lines.append("━" * 40)
        lines.append("")

        # Text body — rewrap to short lines
        text = scene.get("teleprompter_text", "")
        if text:
            for paragraph in text.split("\n"):
                paragraph = paragraph.strip()
                if not paragraph:
                    lines.append("")
                    continue
                # Preserve marker lines as-is
                if paragraph.startswith(("{", "[", "===", "━")):
                    lines.append(paragraph)
                    lines.append("")
                    continue
                # Wrap to words_per_line words per line
                words = paragraph.split()
                for j in range(0, len(words), words_per_line):
                    chunk = " ".join(words[j : j + words_per_line])
                    lines.append(chunk)
                lines.append("")  # blank line after paragraph

        # Block separator
        lines.append("")
        lines.append(f"{PAUSE_BREATH}")
        lines.append("")
        lines.append("")

    # Footer
    lines.append("=" * 40)
    lines.append("  КОНЕЦ ТЕКСТА")
    lines.append("=" * 40)
    lines.append("")

    return "\n".join(lines)


class TeleprompterStep(BaseStep):
    step_name = "teleprompter"

    def execute(self) -> dict:
        script = self.get_previous_step_data("script")

        # Build block descriptions with all available detail
        blocks_info = script.get("blocks", script.get("scenes", []))
        blocks_enriched = []
        for b in blocks_info:
            block_desc = {
                "block_number": b.get("block_number"),
                "block_type": b.get("block_type"),
                "name": b.get("name"),
                "timestamp_start": b.get("timestamp_start"),
                "duration_seconds": b.get("duration_seconds"),
                "talking_points": b.get("talking_points", []),
                "key_phrase": b.get("key_phrase", ""),
                "retention_hook": b.get("retention_hook", ""),
                "transition_to_next": b.get("transition_to_next", ""),
                "visual_direction": b.get("visual_direction", ""),
                "audio_notes": b.get("audio_notes", ""),
            }
            blocks_enriched.append(block_desc)

        pacing = script.get("pacing_notes", "")
        climax_setup = script.get("climax_setup", "")
        key_messages = script.get("key_messages", [])

        # Get recommended duration from research
        research = self.get_previous_step_data("research")
        content_plan = self.get_previous_step_data("content_plan")
        rec_duration = research.get('_recommended_duration_minutes', content_plan.get('target_length_minutes', 12))
        min_words = int(rec_duration * 150)  # 150 words per minute speaking speed

        # Get sources for enrichment
        sources = self.get_previous_step_data("sources")
        sources_context = ""
        if sources and not sources.get("skipped"):
            parts = []
            for q in sources.get("quotes", []):
                parts.append(f"ЦИТАТА для вплетения: \"{q.get('text', '')}\" — {q.get('author', '?')}")
            for s in sources.get("statistics", []):
                parts.append(f"СТАТИСТИКА для упоминания: {s.get('metric', '')} — {s.get('context', '')}")
            for f in sources.get("facts", []):
                parts.append(f"ФАКТ: {f.get('text', '')} (источник: {f.get('source', '?')})")
            if parts:
                sources_context = "\n=== ДАННЫЕ ИЗ ИСТОЧНИКОВ — ВПЛЕТИ В ТЕКСТ ЕСТЕСТВЕННО ===\n" + "\n".join(parts) + "\nВажно: упоминай источники естественно ('по данным McKinsey...', 'как говорит эксперт...')\n"

        prompt = f"""Преобразуй скелет сценария в ПОЛНЫЙ текст для суфлёра.

Заголовок видео: {script.get('title', self.state.topic)}
Тон: {script.get('tone', 'разговорный, дружелюбный')}
ЦЕЛЕВАЯ длительность видео: {rec_duration} минут (на основе анализа конкурентов)
МИНИМУМ слов: {min_words} (скорость речи ~150 слов/мин)
{sources_context}

Ключевые сообщения видео:
{json.dumps(key_messages, ensure_ascii=False, indent=2)}

Заметки по темпу:
{pacing}

Как блоки подводят к кульминации:
{climax_setup}

Блоки сценария (скелет):
{json.dumps(blocks_enriched, ensure_ascii=False, indent=2)}

КРИТИЧЕСКИЕ ТРЕБОВАНИЯ:
1. Пиши ПОЛНЫЙ ТЕКСТ слово в слово — ведущий читает ДОСЛОВНО с суфлёра
2. НЕ тезисы, НЕ план — а готовые фразы и предложения
3. ТОЛЬКО ЧИСТЫЙ ТЕКСТ — никаких эмодзи, спецсимволов, пометок в скобках
4. Между блоками — пустая строка вместо пауз
5. Включи ВСЕ talking points и key_phrase из каждого блока
6. Retention-хуки для удержания зрителя — словами, без маркеров
7. Переходы между блоками — плавные и естественные
8. Минимум {min_words} слов суммарно (для {rec_duration} минут при 150 слов/мин)
9. Речь должна звучать ЕСТЕСТВЕННО, как живой разговор
10. Контекст: Россия, цены в рублях"""

        response = self.ask_claude(SYSTEM_PROMPT, prompt)

        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response[start:end])
            else:
                result = {"raw_response": response}

        # Generate formatted teleprompter .txt file
        if result.get("scenes"):
            formatted_txt = _format_teleprompter_txt(result, TELEPROMPTER_WORDS_PER_LINE)

            txt_path = self.state.project_dir / "teleprompter.txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(formatted_txt)
            result["teleprompter_file"] = str(txt_path)
            logger.info(f"Teleprompter text saved to {txt_path}")

            # Also save the raw full_text if present
            full_text = result.get("full_text", "")
            if full_text:
                raw_path = self.state.project_dir / "teleprompter_raw.txt"
                with open(raw_path, "w", encoding="utf-8") as f:
                    f.write(full_text)
                result["teleprompter_raw_file"] = str(raw_path)

        word_count = result.get("total_word_count", 0)
        if not word_count:
            # Count from scenes
            total = 0
            for scene in result.get("scenes", []):
                total += len(scene.get("teleprompter_text", "").split())
            word_count = total
            result["total_word_count"] = word_count

        scene_count = len(result.get("scenes", []))
        logger.info(
            f"Teleprompter text: {word_count} words, "
            f"{scene_count} scenes, "
            f"~{result.get('estimated_read_time_minutes', '?')} min"
        )

        if word_count < 1200:
            logger.warning(
                f"Teleprompter text may be too short ({word_count} words) "
                f"for a 10+ min video. Consider regenerating."
            )

        return result
