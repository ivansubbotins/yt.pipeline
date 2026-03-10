"""Step 1: Topic research — analyze niche, competitors, trending topics."""

import json
import logging

from steps.base import BaseStep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — YouTube SEO-аналитик и исследователь контента.
Твоя задача — провести глубокое исследование темы для YouTube-канала.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "topic_analysis": "подробный анализ темы",
  "target_audience": "описание целевой аудитории",
  "competitors": [
    {"channel": "название", "strengths": "сильные стороны", "weaknesses": "слабые стороны"}
  ],
  "trending_angles": ["угол 1", "угол 2", "угол 3"],
  "keywords": ["ключевое слово 1", "ключевое слово 2"],
  "recommended_titles": ["вариант заголовка 1", "вариант заголовка 2", "вариант заголовка 3"],
  "estimated_search_volume": "оценка объёма поиска",
  "content_gaps": ["пробел 1", "пробел 2"]
}"""


class ResearchStep(BaseStep):
    step_name = "research"

    def execute(self) -> dict:
        topic = self.state.topic
        if not topic:
            raise ValueError("Topic not set. Set state.topic before running research.")

        prompt = f"""Проведи исследование темы для YouTube-видео:

Тема: {topic}

Требования:
- Видео длинное (10+ минут)
- Нужно найти 3-5 конкурентов по теме
- Определить 5-10 ключевых слов для SEO
- Предложить 3-5 цепляющих заголовков
- Выявить незакрытые ниши (content gaps)
- Определить целевую аудиторию"""

        response = self.ask_claude(SYSTEM_PROMPT, prompt)

        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response[start:end])
            else:
                result = {"raw_response": response}

        logger.info(f"Research completed: {len(result.get('keywords', []))} keywords found")
        return result
