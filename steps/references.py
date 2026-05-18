"""Step 3: Cover references — analyze downloaded competitor thumbnails.

Research step already downloads thumbnails to references/ folder.
This step analyzes them with Claude Vision and generates cover concepts.
"""

import base64
import json
import logging
from pathlib import Path

import anthropic

from config import ANTHROPIC_FALLBACK_MODEL
from steps.base import BaseStep, CLAUDE_PRICING

logger = logging.getLogger(__name__)


ANALYSIS_SYSTEM_PROMPT = """Ты — эксперт по дизайну YouTube-обложек.
Тебе показаны РЕАЛЬНЫЕ обложки конкурентов с YouTube (топ по просмотрам).

Проанализируй их визуально и создай концепты для нашей обложки.

Ответ СТРОГО в формате JSON (без markdown-блоков):
{
  "pattern_analysis": {
    "fonts": {
      "dominant_styles": ["стиль 1", "стиль 2"],
      "text_length": "типичное кол-во слов на обложке",
      "text_position": "где обычно размещён текст",
      "capitalization": "ВСЕ ЗАГЛАВНЫЕ / обычный"
    },
    "colors": {
      "dominant_palettes": [["#hex1", "#hex2"], ["#hex3", "#hex4"]],
      "contrast_level": "высокий/средний/низкий",
      "background_type": "cartoon/фото/градиент/сцена"
    },
    "composition": {
      "layout_patterns": ["паттерн 1", "паттерн 2"],
      "face_present": "да/нет/иногда",
      "face_position": "слева/справа/центр",
      "face_size": "крупный/средний"
    },
    "clickbait_elements": ["элемент 1", "элемент 2"]
  },
  "concepts": [
    {
      "name": "Концепт A",
      "style": "cartoon / photo-realistic / mixed",
      "scene_description": "описание сцены/фона для AI-генерации (на английском)",
      "text_overlay": "ТЕКСТ НА ОБЛОЖКЕ (2-4 слова, русский, КАПС)",
      "text_position": "top-left / top-right / bottom-left / bottom-right / center",
      "text_color": "#FFFFFF",
      "text_stroke_color": "#000000",
      "expert_position": "left / right / none",
      "expert_expression": "удивление / шок / уверенность / злость",
      "background_colors": ["#hex1", "#hex2"],
      "emotion": "какую эмоцию вызывает",
      "inspired_by": "какой референс вдохновил",
      "ctr_score": 8,
      "ctr_reasoning": "почему кликнут"
    }
  ],
  "recommended": "название лучшего концепта",
  "a_b_test_pairs": ["Концепт A", "Концепт B"]
}"""


class ReferencesStep(BaseStep):
    step_name = "references"

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        research = self.get_previous_step_data("research")

        title = content_plan.get("title", self.state.topic)
        thumbnail_text = content_plan.get("thumbnail_text", "")
        thumbnail_emotion = content_plan.get("thumbnail_emotion", "")

        # Find downloaded thumbnails from research step
        refs_dir = self.state.project_dir / "references"
        thumb_files = sorted(refs_dir.glob("*.jpg"), reverse=True) if refs_dir.exists() else []

        # Load raw video data for context
        raw_path = refs_dir / "videos_raw.json"
        videos_data = []
        if raw_path.exists():
            with open(raw_path, "r", encoding="utf-8") as f:
                videos_data = json.load(f)

        # Build image content for Claude Vision (top 10 thumbnails)
        image_contents = []
        top_thumbs = thumb_files[:10]

        for thumb_path in top_thumbs:
            try:
                with open(thumb_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                image_contents.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                })
                # Add label
                # Extract video info from filename
                fname = thumb_path.stem
                # Find matching video
                vid_info = ""
                for v in videos_data:
                    if v.get("video_id", "") in fname:
                        vid_info = (
                            f"{v.get('title', '?')} — "
                            f"{v.get('view_count', 0):,} просмотров, "
                            f"канал: {v.get('channel_title', '?')}"
                        )
                        break
                image_contents.append({
                    "type": "text",
                    "text": f"Обложка: {vid_info or fname}",
                })
            except Exception as e:
                logger.warning(f"Failed to load thumbnail {thumb_path}: {e}")

        # Build prompt
        competitors_info = ""
        for c in research.get("competitors", [])[:5]:
            competitors_info += (
                f"- {c.get('channel', '?')}: \"{c.get('video_title', '?')}\" "
                f"({c.get('views', 0):,} просмотров)\n"
            )

        text_prompt = f"""Проанализируй обложки конкурентов и создай 3 концепта для нашей обложки.

Наше видео: {title}
Тема: {self.state.topic}
Текст для обложки (из контент-плана): {thumbnail_text}
Эмоция обложки: {thumbnail_emotion}

Конкуренты (топ по просмотрам):
{competitors_info}

Анализ обложек из исследования: {research.get('thumbnail_analysis', '')}

ТРЕБОВАНИЯ К КОНЦЕПТАМ:
1. Стиль — cartoon/comic (как у лучших каналов типа Ku-Ku)
2. scene_description — описание сцены НА АНГЛИЙСКОМ для AI-генератора
3. Крупный русский текст (2-4 слова) с обводкой
4. Место для фото эксперта (слева или справа)
5. Яркие контрастные цвета
6. Должно вызывать желание кликнуть"""

        # Send with images if we have them
        if image_contents:
            logger.info(f"Analyzing {len(top_thumbs)} reference thumbnails with Claude Vision...")
            messages_content = image_contents + [{"type": "text", "text": text_prompt}]
        else:
            logger.info("No reference thumbnails found, generating concepts from text only...")
            messages_content = [{"type": "text", "text": text_prompt}]

        # Use Claude Vision API directly (base ask_claude only sends text)
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        system = (
            "Сегодняшняя дата: " + today +
            ". Контекст: Россия, русскоязычная аудитория.\n\n" +
            ANALYSIS_SYSTEM_PROMPT
        )

        def _stream_with(model_name: str) -> tuple[str, object]:
            text_out = ""
            with self.client.messages.stream(
                model=model_name,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": messages_content}],
            ) as stream:
                for text in stream.text_stream:
                    text_out += text
                return text_out, stream.get_final_message()

        try:
            result_text, final_msg = _stream_with(self.model)
            used_model = self.model
        except anthropic.RateLimitError as rl_err:
            if ANTHROPIC_FALLBACK_MODEL and ANTHROPIC_FALLBACK_MODEL != self.model:
                logger.warning(
                    f"Rate-limited on {self.model}, falling back to "
                    f"{ANTHROPIC_FALLBACK_MODEL}: {rl_err}"
                )
                result_text, final_msg = _stream_with(ANTHROPIC_FALLBACK_MODEL)
                used_model = ANTHROPIC_FALLBACK_MODEL
            else:
                raise

        # Track usage so this step's cost shows up in the dashboard
        if final_msg is not None and hasattr(final_msg, "usage") and final_msg.usage:
            inp = final_msg.usage.input_tokens or 0
            out = final_msg.usage.output_tokens or 0
            pricing = CLAUDE_PRICING.get(used_model, {"input": 3.0, "output": 15.0})
            cost = (inp * pricing["input"] + out * pricing["output"]) / 1_000_000
            self._usage_total["input_tokens"] += inp
            self._usage_total["output_tokens"] += out
            self._usage_total["cost_usd"] += cost
            self._usage_total["calls"] += 1
            logger.info(f"  Claude vision call ({used_model}): {inp:,} in + {out:,} out = ${cost:.4f}")

        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            start = result_text.find("{")
            end = result_text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(result_text[start:end])
            else:
                result = {"raw_response": result_text}

        # Attach metadata
        result["references_dir"] = str(refs_dir)
        result["total_references"] = len(thumb_files)
        result["analyzed_thumbnails"] = len(top_thumbs)

        # Save analysis
        analysis_path = refs_dir / "analysis.json"
        refs_dir.mkdir(parents=True, exist_ok=True)
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        concepts_count = len(result.get("concepts", []))
        logger.info(
            f"References analyzed: {len(top_thumbs)} thumbnails, "
            f"{concepts_count} concepts generated"
        )
        return result
