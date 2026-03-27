"""Step 4: Script skeleton — create a detailed script outline with talking points."""

import json
import logging

from steps.base import BaseStep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — профессиональный сценарист YouTube-видео.
Напиши полный скелет сценария для длинного видео (10+ минут).

Скелет ОБЯЗАТЕЛЬНО содержит следующие структурные блоки:
1. ХУК (первые 30 секунд) — мощная зацепка, провокационный вопрос или шок-факт
2. ИНТРО (15-30 сек) — представление темы и обещание ценности
3. ОСНОВНЫЕ БЛОКИ (3-5 блоков по 2-4 минуты) — раскрытие темы по частям
4. КУЛЬМИНАЦИЯ (1-2 минуты) — главный инсайт, «ага-момент»
5. CTA + АУТРО (30-60 сек) — призыв к действию и завершение

Между каждым блоком укажи тип перехода (transition).
В каждом основном блоке укажи retention-хук для удержания зрителя.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "title": "заголовок",
  "total_duration_minutes": 12,
  "blocks": [
    {
      "block_number": 1,
      "block_type": "hook | intro | main | climax | cta_outro",
      "name": "название блока",
      "timestamp_start": "0:00",
      "duration_seconds": 30,
      "format": "talking_head | b_roll | screen_record | mixed",
      "talking_points": ["пункт 1", "пункт 2"],
      "key_phrase": "ключевая фраза, которую ведущий должен произнести",
      "visual_direction": "описание того, что на экране",
      "audio_notes": "музыка, звуковые эффекты",
      "retention_hook": "хук удержания (для основных блоков)",
      "transition_to_next": "тип и описание перехода к следующему блоку"
    }
  ],
  "key_messages": ["ключевое сообщение 1", "ключевое сообщение 2"],
  "tone": "описание тона видео",
  "pacing_notes": "заметки по темпу — где ускориться, где замедлиться",
  "climax_setup": "как основные блоки подводят к кульминации"
}"""


class ScriptStep(BaseStep):
    step_name = "script"

    def _build_cta_instructions(self) -> str:
        """Build CTA instructions from channel context."""
        ctx = self.get_channel_context()
        if not ctx:
            return ""

        lines = []
        cta = ctx.get("cta", {})

        lead = cta.get("lead_magnet", {})
        if lead.get("enabled") and lead.get("text"):
            placement = lead.get("placement", "intro")
            lines.append(
                f"- ЛИДМАГНИТ (вставить в {placement}): "
                f"Автор должен произнести: \"{lead['text']}\""
            )

        mid = cta.get("mid_roll", {})
        if mid.get("enabled") and mid.get("text"):
            block_num = mid.get("placement_after_block", 3)
            lines.append(
                f"- РЕКЛАМНАЯ ВСТАВКА (после блока {block_num}): "
                f"Автор должен произнести: \"{mid['text']}\""
            )

        end = cta.get("end_screen", {})
        if end.get("enabled") and end.get("text"):
            lines.append(
                f"- КОНЦОВКА: \"{end['text']}\""
            )

        if not lines:
            return ""
        return "\n\nCTA-вставки (ОБЯЗАТЕЛЬНО включить в сценарий):\n" + "\n".join(lines)

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        research = self.get_previous_step_data("research")
        sources = self.get_previous_step_data("sources")

        cta_instructions = self._build_cta_instructions()

        # Build sources context if available
        sources_context = ""
        if sources and not sources.get("skipped"):
            parts = []
            for f in sources.get("facts", []):
                parts.append(f"ФАКТ: {f.get('text', '')} (источник: {f.get('source', '?')})")
            for q in sources.get("quotes", []):
                parts.append(f"ЦИТАТА: \"{q.get('text', '')}\" — {q.get('author', '?')} ({q.get('source', '')})")
            for s in sources.get("statistics", []):
                parts.append(f"СТАТИСТИКА: {s.get('metric', '')} — {s.get('context', '')} (источник: {s.get('source', '?')})")
            for ins in sources.get("key_insights", []):
                parts.append(f"ИНСАЙТ: {ins}")
            if parts:
                sources_context = "\n\n=== РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИСТОЧНИКОВ ===\n" + "\n".join(parts) + "\nВАЖНО: используй эти данные в сценарии! НЕ выдумывай факты — бери из источников.\n"

        rec_duration = research.get('_recommended_duration_minutes', content_plan.get('target_length_minutes', 12))

        prompt = f"""Напиши скелет сценария для YouTube-видео.

Заголовок: {content_plan.get('title', self.state.topic)}
Тема: {self.state.topic}

Контент-план:
- Хук: {content_plan.get('hook', '')}
- Структура: {json.dumps(content_plan.get('structure', []), ensure_ascii=False)}
- CTA: {content_plan.get('cta', '')}
- Retention-хуки: {json.dumps(content_plan.get('retention_hooks', []), ensure_ascii=False)}
- B-roll идеи: {json.dumps(content_plan.get('b_roll_ideas', []), ensure_ascii=False)}

Ключевые слова для SEO: {json.dumps(research.get('keywords', []), ensure_ascii=False)}
{sources_context}
Требования:
- Целевой хронометраж: {rec_duration} минут (на основе анализа конкурентов)
- Обязательные блоки: хук (30 сек), интро, 3-5 основных блоков, кульминация, CTA+аутро
- Тайминг (timestamp_start) и длительность (duration_seconds) для КАЖДОГО блока
- Ключевые переходы (transition_to_next) между блоками
- Конкретные talking points для каждого блока
- Retention-хук в каждом основном блоке
- Ключевая фраза (key_phrase) — дословно что произнести
- Визуальные указания для оператора/монтажёра
- Естественный, разговорный тон
- Кульминация должна быть логическим итогом основных блоков{cta_instructions}"""

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

        blocks_count = len(result.get("blocks", []))
        total_min = result.get("total_duration_minutes", "?")
        logger.info(f"Script skeleton created: {blocks_count} blocks, ~{total_min} min")

        # Validate required block types are present
        block_types = {b.get("block_type") for b in result.get("blocks", [])}
        required_types = {"hook", "main", "climax", "cta_outro"}
        missing = required_types - block_types
        if missing:
            logger.warning(f"Script skeleton missing block types: {missing}")

        return result
