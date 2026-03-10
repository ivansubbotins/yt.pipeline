"""Step 5: Teleprompter text — convert script to teleprompter-friendly format."""

import json
import logging

from steps.base import BaseStep
from config import TELEPROMPTER_WORDS_PER_LINE, TELEPROMPTER_PAUSE_MARKER, TELEPROMPTER_EMPHASIS_MARKER

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — специалист по подготовке текста для суфлёра (телепромптера).
Преобразуй сценарий в текст для суфлёра.

Правила:
- Короткие строки (5-7 слов на строку) — легко читать с расстояния
- Паузы обозначай: ⏸️ (1 секунда) или ⏸️⏸️ (2 секунды)
- Акценты/ударения: ➡️ СЛОВО ➡️
- Каждая сцена начинается с заголовка в квадратных скобках: [СЦЕНА: Название]
- Визуальные подсказки в фигурных скобках: {посмотреть в камеру}
- Новый абзац = длинная пауза

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "scenes": [
    {
      "scene_name": "название сцены",
      "teleprompter_text": "текст для суфлёра с разметкой"
    }
  ],
  "total_word_count": 1500,
  "estimated_read_time_minutes": 10,
  "full_text": "полный текст для суфлёра целиком"
}"""


class TeleprompterStep(BaseStep):
    step_name = "teleprompter"

    def execute(self) -> dict:
        script = self.get_previous_step_data("script")

        prompt = f"""Преобразуй сценарий в текст для суфлёра.

Заголовок: {script.get('title', self.state.topic)}
Тон: {script.get('tone', 'разговорный')}

Блоки сценария:
{json.dumps(script.get('blocks', script.get('scenes', [])), ensure_ascii=False, indent=2)}

Требования:
- {TELEPROMPTER_WORDS_PER_LINE} слов на строку
- Паузы: {TELEPROMPTER_PAUSE_MARKER}
- Акценты: {TELEPROMPTER_EMPHASIS_MARKER} СЛОВО {TELEPROMPTER_EMPHASIS_MARKER}
- Полный текст, НЕ тезисы — ведущий читает дословно
- Речь должна звучать естественно, не как написанный текст
- Включи все talking points из сценария"""

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

        # Save teleprompter text as a separate .txt file for easy access
        full_text = result.get("full_text", "")
        if full_text:
            txt_path = self.state.project_dir / "teleprompter.txt"
            with open(txt_path, "w") as f:
                f.write(full_text)
            result["teleprompter_file"] = str(txt_path)
            logger.info(f"Teleprompter text saved to {txt_path}")

        word_count = result.get("total_word_count", len(full_text.split()))
        logger.info(f"Teleprompter text: {word_count} words")
        return result
