"""Step 6: Covers — generate clickbait thumbnail images (1280x720).

Generates 3 thumbnail variants using AI image generation (Recraft API)
with Pillow text overlay. Falls back to Pillow-only gradient mode if
no Recraft API key is configured.

Performs CTR analysis to recommend the best variant for A/B testing.
"""

import json
import logging

from steps.base import BaseStep
from config import THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, RECRAFT_API_KEY
from thumbnail_generator import generate_all_variants

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — генератор промптов для AI-генерации изображений и эксперт по YouTube CTR.
Создай детальные промпты для генерации YouTube-обложек и проведи CTR-анализ.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "thumbnails": [
    {
      "name": "Вариант A",
      "generation_prompt": "детальный промпт для генерации изображения (на английском, описывай визуал: объекты, освещение, стиль, композицию, НЕ текст)",
      "text_overlay": "КРУПНЫЙ ТЕКСТ НА ОБЛОЖКЕ (2-4 слова, русский)",
      "text_position": "center",
      "text_color": "#FFFFFF",
      "text_stroke_color": "#000000",
      "font_style": "bold, sans-serif",
      "colors": ["#hex1", "#hex2", "#hex3"],
      "description": "описание концепта",
      "ctr_score": 8,
      "ctr_reasoning": "почему этот вариант привлечёт клик"
    }
  ],
  "best_variant": "Вариант A",
  "best_variant_index": 0,
  "ctr_analysis": "общий анализ: почему выбран лучший вариант, что делает его кликабельным"
}

Правила для промптов:
- Промпт на АНГЛИЙСКОМ (для AI-генератора)
- Описывай визуальный стиль: яркий, контрастный, профессиональный
- Указывай композицию: объект на переднем плане, размытый фон
- Упоминай освещение: драматическое, студийное, неоновое
- НЕ включай текст в промпт — текст накладывается отдельно
- Стиль: digital illustration или photo-realistic
- CTR score от 1 до 10"""


class CoversStep(BaseStep):
    step_name = "covers"

    def execute(self) -> dict:
        references = self.get_previous_step_data("references")
        content_plan = self.get_previous_step_data("content_plan")

        concepts = references.get("concepts", [])
        recommended = references.get("recommended", "")

        prompt = f"""Создай 3 промпта для генерации YouTube-обложек на основе концептов.

Заголовок видео: {content_plan.get('title', self.state.topic)}

Концепты обложек:
{json.dumps(concepts, ensure_ascii=False, indent=2)}

Рекомендованный вариант: {recommended}

Технические требования:
- Размер: {THUMBNAIL_WIDTH}x{THUMBNAIL_HEIGHT}px
- Формат: JPEG
- Яркие контрастные цвета
- Крупный текст (2-4 слова, РУССКИЙ)
- Лицо с эмоцией или яркий объект (зависит от концепта)
- Кликбейт-стиль: должно вызывать желание кликнуть

Проведи CTR-анализ каждого варианта (оценка 1-10) и выбери лучший."""

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

        thumbnails = result.get("thumbnails", [])

        # Save prompts to files
        prompts_dir = self.state.project_dir / "cover_prompts"
        prompts_dir.mkdir(exist_ok=True)
        for i, thumb in enumerate(thumbnails):
            prompt_file = prompts_dir / f"thumbnail_{i + 1}.txt"
            with open(prompt_file, "w") as f:
                f.write(f"Name: {thumb.get('name', f'Variant {i + 1}')}\n")
                f.write(f"Prompt: {thumb.get('generation_prompt', '')}\n")
                f.write(f"Text overlay: {thumb.get('text_overlay', '')}\n")
                f.write(f"Position: {thumb.get('text_position', 'center')}\n")
                f.write(f"Colors: {', '.join(thumb.get('colors', []))}\n")
                f.write(f"CTR Score: {thumb.get('ctr_score', 'N/A')}\n")
                f.write(f"CTR Reasoning: {thumb.get('ctr_reasoning', '')}\n")

        # Generate actual thumbnail images
        thumbnails_dir = self.state.project_dir / "thumbnails"
        use_recraft = bool(RECRAFT_API_KEY)

        mode = "Recraft API" if use_recraft else "Pillow (fallback)"
        logger.info(f"Generating {len(thumbnails)} thumbnails using {mode}")

        generated_paths = generate_all_variants(
            thumbnails, thumbnails_dir, use_recraft=use_recraft
        )

        # Mark best variant
        best_index = result.get("best_variant_index", 0)
        if 0 <= best_index < len(generated_paths):
            best_path = generated_paths[best_index]
            # Copy best variant as the primary thumbnail
            import shutil
            primary_path = self.state.project_dir / "thumbnail.jpg"
            shutil.copy2(best_path, primary_path)
            logger.info(f"Best variant (#{best_index + 1}) saved as {primary_path}")
            result["primary_thumbnail"] = str(primary_path)

        result["generated_files"] = [str(p) for p in generated_paths]
        result["generation_mode"] = mode

        logger.info(
            f"Generated {len(generated_paths)} thumbnail images, "
            f"best variant: {result.get('best_variant', 'N/A')} "
            f"(CTR analysis: {result.get('ctr_analysis', 'N/A')[:100]})"
        )
        return result
