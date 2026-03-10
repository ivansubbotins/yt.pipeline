"""Step 7: Description — generate YouTube video description with SEO optimization."""

import json
import logging

from steps.base import BaseStep
from config import DESCRIPTION_TEMPLATE

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — YouTube SEO-копирайтер.
Напиши оптимизированное описание для YouTube-видео.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "title": "финальный заголовок видео",
  "description": "полный текст описания",
  "summary": "краткое описание (1-2 предложения)",
  "timestamps": [
    {"time": "0:00", "label": "Вступление"},
    {"time": "1:30", "label": "Основная часть"}
  ],
  "keywords": ["ключевое слово 1", "ключевое слово 2"],
  "tags": ["тег1", "тег2", "тег3"],
  "hashtags": ["хэштег1", "хэштег2", "хэштег3"],
  "links": ["ссылка 1 описание", "ссылка 2 описание"],
  "cta_text": "текст призыва к действию"
}"""


class DescriptionStep(BaseStep):
    step_name = "description"

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        research = self.get_previous_step_data("research")
        script = self.get_previous_step_data("script")

        # Build timestamps from script scenes
        scenes = script.get("scenes", [])
        scenes_info = "\n".join(
            f"- {s.get('name', f'Scene {s.get(\"scene_number\", i)}')}: {s.get('duration_seconds', 120)}s"
            for i, s in enumerate(scenes)
        )

        prompt = f"""Напиши описание для YouTube-видео.

Заголовок: {content_plan.get('title', self.state.topic)}
Тема: {self.state.topic}

Ключевые слова SEO: {json.dumps(research.get('keywords', []), ensure_ascii=False)}
Теги: {json.dumps(content_plan.get('tags', []), ensure_ascii=False)}

Структура видео (для таймкодов):
{scenes_info}

CTA: {content_plan.get('cta', '')}

Требования:
- Первые 2 строки — самые важные (отображаются до "Ещё")
- Таймкоды (timestamps) для навигации
- 10-15 ключевых слов естественно вписанных в текст
- Хэштеги (3-5 штук)
- Призыв к действию (подписка, лайк, комментарий)
- Ссылки-заглушки для соцсетей и полезных ресурсов"""

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

        # Save description to file
        desc_file = self.state.project_dir / "description.txt"
        with open(desc_file, "w") as f:
            f.write(result.get("description", ""))
        result["description_file"] = str(desc_file)

        logger.info("Description generated and saved")
        return result
