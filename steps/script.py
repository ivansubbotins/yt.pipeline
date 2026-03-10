"""Step 4: Script skeleton — create a detailed script outline with talking points."""

import json
import logging

from steps.base import BaseStep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — профессиональный сценарист YouTube-видео.
Напиши полный скелет сценария для длинного видео (10+ минут).

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "title": "заголовок",
  "total_duration_minutes": 12,
  "scenes": [
    {
      "scene_number": 1,
      "name": "Хук / Интро",
      "duration_seconds": 30,
      "type": "talking_head | b_roll | screen_record | mixed",
      "talking_points": ["пункт 1", "пункт 2"],
      "visual_direction": "описание того, что на экране",
      "audio_notes": "музыка, звуковые эффекты",
      "transition": "тип перехода к следующей сцене"
    }
  ],
  "key_messages": ["ключевое сообщение 1", "ключевое сообщение 2"],
  "tone": "описание тона видео",
  "pacing_notes": "заметки по темпу"
}"""


class ScriptStep(BaseStep):
    step_name = "script"

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        research = self.get_previous_step_data("research")

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

Требования:
- Минимум 10 минут хронометража
- Конкретные talking points для каждой сцены
- Визуальные указания для оператора/монтажёра
- Retention-хуки через каждые 2-3 минуты
- Естественный, разговорный тон"""

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

        scenes_count = len(result.get("scenes", []))
        logger.info(f"Script created: {scenes_count} scenes")
        return result
