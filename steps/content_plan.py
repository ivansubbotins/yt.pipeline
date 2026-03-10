"""Step 2: Content plan — create a structured content plan for the video."""

import json
import logging

from steps.base import BaseStep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — YouTube-стратег и контент-планировщик.
Создай детальный контент-план для длинного YouTube-видео (10+ минут).

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "title": "финальный заголовок видео",
  "hook": "хук первых 30 секунд — чем зацепить зрителя",
  "target_length_minutes": 12,
  "structure": [
    {
      "section": "название раздела",
      "duration_minutes": 2,
      "key_points": ["пункт 1", "пункт 2"],
      "visual_notes": "заметки по визуалу"
    }
  ],
  "cta": "призыв к действию",
  "retention_hooks": ["хук удержания 1", "хук удержания 2"],
  "b_roll_ideas": ["идея для перебивки 1", "идея для перебивки 2"],
  "tags": ["тег1", "тег2"]
}"""


class ContentPlanStep(BaseStep):
    step_name = "content_plan"

    def execute(self) -> dict:
        research = self.get_previous_step_data("research")

        prompt = f"""Создай контент-план для YouTube-видео.

Тема: {self.state.topic}

Данные исследования:
- Ключевые слова: {json.dumps(research.get('keywords', []), ensure_ascii=False)}
- Рекомендованные заголовки: {json.dumps(research.get('recommended_titles', []), ensure_ascii=False)}
- Целевая аудитория: {research.get('target_audience', 'не определена')}
- Незакрытые ниши: {json.dumps(research.get('content_gaps', []), ensure_ascii=False)}
- Трендовые подходы: {json.dumps(research.get('trending_angles', []), ensure_ascii=False)}

Требования:
- Минимум 10 минут хронометража
- Сильный хук в первые 30 секунд
- Retention-хуки каждые 2-3 минуты
- Чёткая структура с таймингами
- CTA (подписка, лайк, комментарий)"""

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

        logger.info(f"Content plan created: {result.get('title', 'untitled')}")
        return result
