"""Step 3: Cover references — generate thumbnail concepts and reference descriptions."""

import json
import logging

from steps.base import BaseStep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — дизайнер YouTube-обложек (thumbnails).
Создай детальные описания обложек для YouTube-видео.

Стиль: кликбейтный, но не обманчивый.
Формат: 1280x720px.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "concepts": [
    {
      "name": "Вариант A",
      "description": "Описание концепта",
      "text_overlay": "Текст на обложке (2-4 слова, КРУПНЫЙ)",
      "background": "описание фона",
      "colors": ["#hex1", "#hex2", "#hex3"],
      "emotion": "какую эмоцию вызывает",
      "face_expression": "выражение лица (если есть лицо)",
      "layout": "описание расположения элементов",
      "contrast_trick": "как привлечь внимание"
    }
  ],
  "recommended": "название лучшего варианта",
  "a_b_test_pairs": ["Вариант A", "Вариант B"]
}"""


class ReferencesStep(BaseStep):
    step_name = "references"

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")

        prompt = f"""Создай 3 концепта обложек для YouTube-видео.

Заголовок: {content_plan.get('title', self.state.topic)}
Тема: {self.state.topic}
Целевая аудитория: видеоформат 10+ минут

Требования:
- Крупный текст (2-4 слова максимум на обложке)
- Яркие, контрастные цвета
- Эмоция (удивление, любопытство, шок)
- Формат 1280x720
- Должно быть понятно о чём видео даже без заголовка
- Кликбейт-стиль, но не обманчивый"""

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

        logger.info(f"Created {len(result.get('concepts', []))} thumbnail concepts")
        return result
