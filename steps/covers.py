"""Step 6: Covers — generate 3-layer clickbait thumbnails (1280x720).

Uses the 3-layer system:
1. AI-generated cartoon scene (Recraft/Flux)
2. Expert photo cutout (rembg)
3. Bold Russian text with outline (Pillow)

Claude generates prompts based on reference analysis, then thumbnail_generator
assembles all layers.
"""

import json
import logging
import shutil

from steps.base import BaseStep
from config import THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, RECRAFT_API_KEY, FAL_KEY, BASE_DIR
from thumbnail_generator import generate_all_variants

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — эксперт по YouTube-обложкам с высоким CTR.

Создай 3 промпта для AI-генерации ПОЛНЫХ обложек YouTube-видео через Nano Banana 2.

ВАЖНО: Фото эксперта загружается как входное изображение. Промпт описывает ПОЛНУЮ СЦЕНУ — включая позу человека, текст на обложке, фон, эффекты и освещение. Nano Banana сохранит черты лица с фото.

Каждый вариант должен отличаться:
- Позиция эксперта: центр / слева / справа
- Разный текст на обложке (2-4 слова, русский, провокационный)
- Разные эмоции/позы: крик, шок, уверенность, указывает пальцем
- Разные цвета неоновой подсветки для контраста

Ответ СТРОГО в формате JSON (без markdown-блоков):
{
  "thumbnails": [
    {
      "name": "Вариант A",
      "generation_prompt": "Detailed English prompt describing the FULL thumbnail scene. Include: the man's pose and emotion, background scene, neon lighting color and effects, large bold text behind/around the speaker. Style: vibrant YouTube thumbnail, digital illustration, dramatic neon glow. Example: 'A man screaming at the camera, holding his head with both hands, standing in the center. Bold neon blue glow behind him. Large text behind the speaker says [TEXT]. Dramatic lighting, YouTube thumbnail style, 16:9 landscape.'",
      "text_on_image": "КРУПНЫЙ ТЕКСТ НА ОБЛОЖКЕ (2-4 слова, русский)",
      "expert_position": "center",
      "expert_emotion": "крик / шок / уверенность / указывает пальцем",
      "neon_color": "#00BFFF",
      "strength": 0.5,
      "style": "digital_illustration",
      "description": "описание концепта",
      "ctr_score": 8,
      "ctr_reasoning": "почему кликнут"
    }
  ],
  "best_variant": "Вариант A",
  "best_variant_index": 0,
  "ctr_analysis": "общий анализ: почему этот вариант лучший"
}

Правила для generation_prompt:
- ТОЛЬКО на АНГЛИЙСКОМ
- Описывай ПОЛНУЮ сцену: человек + фон + текст + эффекты
- ОБЯЗАТЕЛЬНО неоновая подсветка (neon glow) контрастного цвета
- Текст на обложке — включай прямо в промпт как 'Large bold text says "[ТЕКСТ]"'
- Указывай позу и эмоцию человека (screaming, shocked, pointing, confident)
- Стиль: vibrant YouTube thumbnail, digital illustration, dramatic lighting
- Размер: 16:9 landscape orientation
- Nano Banana 2 отлично рисует текст на картинке — используй это"""


class CoversStep(BaseStep):
    step_name = "covers"

    def execute(self) -> dict:
        references = self.get_previous_step_data("references")
        content_plan = self.get_previous_step_data("content_plan")
        research = self.get_previous_step_data("research")

        # Cover concepts from references step
        concepts = references.get("concepts", [])
        recommended = references.get("recommended", "")
        pattern_analysis = references.get("pattern_analysis", {})

        # Thumbnail suggestions from content plan
        thumbnail_text = content_plan.get("thumbnail_text", "")
        thumbnail_emotion = content_plan.get("thumbnail_emotion", "")

        prompt = f"""Создай 3 промпта для генерации YouTube-обложек через Nano Banana 2.

Фото эксперта уже загружено — Nano Banana сохранит его лицо. Промпт должен описывать ПОЛНУЮ сцену.

Заголовок видео: {content_plan.get('title', self.state.topic)}
Тема: {self.state.topic}

Текст для обложки (из контент-плана): {thumbnail_text}
Эмоция обложки: {thumbnail_emotion}

Концепты из анализа референсов:
{json.dumps(concepts, ensure_ascii=False, indent=2)}

Рекомендованный концепт: {recommended}

Паттерны конкурентов:
{json.dumps(pattern_analysis, ensure_ascii=False, indent=2)}

Анализ обложек из исследования:
{research.get('thumbnail_analysis', 'нет данных')}

ТРЕБОВАНИЯ:
1. 3 разных варианта: эксперт в центре / слева / справа
2. Каждый вариант — ПОЛНАЯ сцена (человек + фон + текст + неон)
3. ОБЯЗАТЕЛЬНО неоновая подсветка контрастного цвета за спиной эксперта
4. Текст прямо на обложке — включай в промпт как 'Large bold text says "[ТЕКСТ]"'
5. Текст — 2-4 слова, КРУПНЫЙ, провокационный, на русском
6. Разные эмоции/позы в каждом варианте
7. CTR-анализ каждого варианта"""

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

        # Save prompts
        prompts_dir = self.state.project_dir / "cover_prompts"
        prompts_dir.mkdir(exist_ok=True)
        for i, thumb in enumerate(thumbnails):
            prompt_file = prompts_dir / f"thumbnail_{i + 1}.json"
            with open(prompt_file, "w", encoding="utf-8") as f:
                json.dump(thumb, f, ensure_ascii=False, indent=2)

        # Find expert photo (check project dir and pipeline root)
        expert_photo = self._find_expert_photo()
        style_image = self._find_selected_style()

        # Generate thumbnails
        thumbnails_dir = self.state.project_dir / "thumbnails"
        use_recraft = bool(RECRAFT_API_KEY)
        use_i2i = bool(expert_photo and FAL_KEY)

        if use_i2i:
            mode = "Nano Banana 2 (fal.ai)"
            if style_image:
                mode += f" + style ref"
            logger.info(f"Expert photo found: {expert_photo} — using Nano Banana 2")
        elif expert_photo:
            mode = "Recraft + Expert + Text (3-layer)"
            logger.info(f"Expert photo found: {expert_photo}")
        else:
            mode = "Recraft scene only"
            logger.info("No expert photo — generating scene only")

        logger.info(f"Generating {len(thumbnails)} thumbnails [{mode}]")

        generated_paths = generate_all_variants(
            thumbnails, thumbnails_dir,
            use_recraft=use_recraft,
            expert_photo_path=expert_photo,
            use_i2i=use_i2i,
            style_image_path=style_image,
        )

        # Copy best variant as primary
        best_index = result.get("best_variant_index", 0)
        if 0 <= best_index < len(generated_paths):
            best_path = generated_paths[best_index]
            primary_path = self.state.project_dir / "thumbnail.jpg"
            shutil.copy2(best_path, primary_path)
            logger.info(f"Best variant (#{best_index + 1}) → {primary_path}")
            result["primary_thumbnail"] = str(primary_path)

        result["generated_files"] = [str(p) for p in generated_paths]
        result["generation_mode"] = mode
        result["has_expert_photo"] = bool(expert_photo)

        logger.info(
            f"Generated {len(generated_paths)} thumbnails, "
            f"best: {result.get('best_variant', 'N/A')}"
        )
        return result

    def _find_selected_style(self) -> str | None:
        """Find the selected style reference image."""
        styles_meta = BASE_DIR / "assets" / "styles" / "styles.json"
        if not styles_meta.exists():
            return None
        try:
            styles = json.loads(styles_meta.read_text(encoding="utf-8"))
        except Exception:
            return None

        # Check if a style is selected in channel context
        from config import load_channel_context
        ctx = load_channel_context()
        selected_id = ctx.get("selected_style", "")

        if not selected_id and styles:
            # No selection — use the first style as default
            selected_id = styles[0].get("id", "")

        if not selected_id:
            return None

        for style in styles:
            if style.get("id") == selected_id:
                img_path = BASE_DIR / "assets" / "styles" / style["file"]
                if img_path.exists():
                    logger.info(f"Using style reference: {style['name']} ({img_path})")
                    return str(img_path)
        return None

    def _find_expert_photo(self) -> str | None:
        """Find expert photo in project or pipeline directory."""
        from config import BASE_DIR
        search_paths = [
            self.state.project_dir / "expert.jpg",
            self.state.project_dir / "expert.png",
            self.state.project_dir / "expert_photo.jpg",
            self.state.project_dir / "expert_photo.png",
            # Pipeline root assets (where web UI uploads)
            BASE_DIR / "assets" / "expert.jpg",
            BASE_DIR / "assets" / "expert.png",
            # Pipeline root
            BASE_DIR / "expert.jpg",
            BASE_DIR / "expert.png",
            # Data dir assets (legacy)
            self.state.project_dir.parent / "assets" / "expert.jpg",
            self.state.project_dir.parent / "assets" / "expert.png",
        ]
        for p in search_paths:
            if p.exists():
                return str(p)
        return None
