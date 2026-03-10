"""Step 6: Covers — generate thumbnail images using AI image generation."""

import json
import logging

from steps.base import BaseStep
from config import THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — генератор промптов для AI-генерации изображений.
Создай детальные промпты для генерации YouTube-обложек.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "thumbnails": [
    {
      "name": "Вариант A",
      "generation_prompt": "детальный промпт для генерации (DALL-E/Midjourney/Stable Diffusion)",
      "text_overlay": "текст, который нужно наложить поверх изображения",
      "text_position": "top-left | top-right | center | bottom-left | bottom-right",
      "text_color": "#FFFFFF",
      "text_stroke_color": "#000000",
      "font_style": "bold, sans-serif"
    }
  ],
  "notes": "дополнительные заметки для дизайнера"
}"""


class CoversStep(BaseStep):
    step_name = "covers"

    def execute(self) -> dict:
        references = self.get_previous_step_data("references")
        content_plan = self.get_previous_step_data("content_plan")

        concepts = references.get("concepts", [])
        recommended = references.get("recommended", "")

        prompt = f"""Создай промпты для генерации YouTube-обложек на основе концептов.

Заголовок видео: {content_plan.get('title', self.state.topic)}

Концепты обложек:
{json.dumps(concepts, ensure_ascii=False, indent=2)}

Рекомендованный вариант: {recommended}

Технические требования:
- Размер: {THUMBNAIL_WIDTH}x{THUMBNAIL_HEIGHT}px
- Формат: JPEG
- Яркие контрастные цвета
- Крупный текст (2-4 слова)
- Лицо с эмоцией (если предусмотрено концептом)

Создай готовые промпты для генерации каждого варианта."""

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

        # Save prompts to files
        prompts_dir = self.state.project_dir / "cover_prompts"
        prompts_dir.mkdir(exist_ok=True)
        for i, thumb in enumerate(result.get("thumbnails", [])):
            prompt_file = prompts_dir / f"thumbnail_{i + 1}.txt"
            with open(prompt_file, "w") as f:
                f.write(f"Name: {thumb.get('name', f'Variant {i+1}')}\n")
                f.write(f"Prompt: {thumb.get('generation_prompt', '')}\n")
                f.write(f"Text overlay: {thumb.get('text_overlay', '')}\n")
                f.write(f"Position: {thumb.get('text_position', 'center')}\n")

        logger.info(f"Generated {len(result.get('thumbnails', []))} thumbnail prompts")
        return result
