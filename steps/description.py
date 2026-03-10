"""Step 7: Description — generate SEO-optimized YouTube video description.

Generates a complete, channel-consistent description with:
- First 2 lines optimized for "show more" preview
- Accurate timestamps from script blocks
- SEO keywords naturally integrated
- Hashtags, tags, links, CTA
- Uniform format across all channel videos
"""

import json
import logging

from steps.base import BaseStep
from config import DESCRIPTION_TEMPLATE

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — YouTube SEO-копирайтер с опытом оптимизации описаний для максимального охвата и CTR.

Твоя задача — написать SEO-оптимизированное описание для YouTube-видео, следуя ЕДИНОМУ формату канала.

## Правила SEO для YouTube Description

### Первые 2 строки (КРИТИЧЕСКИ ВАЖНО)
- Видны ДО нажатия «Ещё» — это твой единственный шанс привлечь внимание
- Должны содержать главное ключевое слово и ценность видео
- Максимум 150 символов на первую строку
- Вторая строка — усиление интриги или обещание конкретной выгоды

### Основной текст (body)
- 3-5 абзацев, раскрывающих содержание видео
- Ключевые слова вписаны ЕСТЕСТВЕННО, не спамово
- Упомяни конкретные темы/вопросы, которые раскрываются в видео
- Используй эмодзи для структурирования (📌, 🔑, ⏱, 👉, 🔔, 💡, 🎯)

### Таймкоды
- ОБЯЗАТЕЛЬНО для каждого смыслового блока видео
- Формат: "M:SS Название" (без тире перед временем)
- Первый таймкод ВСЕГДА "0:00 Вступление" или аналог
- Названия таймкодов — кликабельные и интригующие (не сухие)
- YouTube автоматически создаёт навигацию по таймкодам

### Ключевые слова
- 10-15 релевантных ключевых слов
- Основное ключевое слово — в первом предложении
- Длиннохвостые запросы (long-tail) вплетены в текст
- НЕ просто список, а осмысленные фразы

### Теги (tags)
- 15-25 тегов для YouTube (используются при загрузке)
- От широких к узким: общая тема → конкретная ниша
- Включи вариации написания и синонимы
- Бренд-теги канала

### Хэштеги
- 3-5 хэштегов в конце описания
- Первые 3 отображаются НАД заголовком видео
- Без пробелов внутри, CamelCase для читаемости

### CTA (призыв к действию)
- Подписка + колокольчик
- Лайк + комментарий с конкретным вопросом
- Ссылки на соцсети и связанный контент

### Ссылки
- Укажи заглушки для: Telegram, соцсети, полезные ресурсы по теме
- Формат: "📌 Название: [ссылка]"

## Формат JSON-ответа (без markdown-блоков):
{
  "title": "финальный SEO-заголовок видео",
  "first_line": "первая строка описания (до Ещё) — с главным ключом",
  "second_line": "вторая строка — усиление интриги",
  "body": "основной текст описания (3-5 абзацев с эмодзи и ключами)",
  "timestamps": [
    {"time": "0:00", "label": "Вступление"},
    {"time": "0:30", "label": "Название блока"},
    {"time": "3:15", "label": "Следующий блок"}
  ],
  "keywords": ["ключевое слово 1", "ключевое слово 2"],
  "tags": ["тег1", "тег2", "тег3"],
  "hashtags": ["ХэштегОдин", "ХэштегДва", "ХэштегТри"],
  "links": [
    {"label": "Telegram-канал", "url": "[ССЫЛКА]"},
    {"label": "Полезный ресурс", "url": "[ССЫЛКА]"}
  ],
  "cta_text": "текст призыва к действию",
  "cta_question": "вопрос для комментариев",
  "summary": "краткое описание для внутреннего использования (1-2 предложения)"
}"""


def _calculate_timestamps(blocks: list[dict]) -> list[dict]:
    """Calculate accurate timestamps from script blocks' duration data."""
    timestamps = []
    current_seconds = 0

    for block in blocks:
        minutes = current_seconds // 60
        seconds = current_seconds % 60
        time_str = f"{minutes}:{seconds:02d}"

        name = block.get("name", f"Блок {block.get('block_number', '?')}")
        timestamps.append({"time": time_str, "label": name})

        duration = block.get("duration_seconds", 120)
        current_seconds += duration

    return timestamps


def _format_description_file(result: dict) -> str:
    """Format the final description text file in the unified channel format."""
    lines = []

    # First 2 lines (above "Show more")
    lines.append(result.get("first_line", result.get("title", "")))
    second = result.get("second_line", "")
    if second:
        lines.append(second)
    lines.append("")

    # Body text
    body = result.get("body", "")
    if body:
        lines.append(body)
        lines.append("")

    # Timestamps
    timestamps = result.get("timestamps", [])
    if timestamps:
        lines.append("⏱ Таймкоды:")
        for ts in timestamps:
            lines.append(f"{ts['time']} {ts['label']}")
        lines.append("")

    # Keywords line
    keywords = result.get("keywords", [])
    if keywords:
        lines.append(f"🔑 Ключевые слова: {', '.join(keywords)}")
        lines.append("")

    # Links
    links = result.get("links", [])
    if links:
        lines.append("📌 Полезные ссылки:")
        for link in links:
            if isinstance(link, dict):
                lines.append(f"  {link.get('label', '')}: {link.get('url', '[ССЫЛКА]')}")
            else:
                lines.append(f"  {link}")
        lines.append("")

    # CTA
    cta = result.get("cta_text", "")
    if cta:
        lines.append(cta)
        lines.append("")

    cta_q = result.get("cta_question", "")
    if cta_q:
        lines.append(f"💬 {cta_q}")
        lines.append("")

    # Subscribe CTA
    lines.append("👉 Подписывайтесь на канал и нажмите 🔔, чтобы не пропустить новые видео!")
    lines.append("")

    # Hashtags
    hashtags = result.get("hashtags", [])
    if hashtags:
        hashtag_str = " ".join(
            f"#{h}" if not h.startswith("#") else h for h in hashtags
        )
        lines.append(hashtag_str)

    return "\n".join(lines)


class DescriptionStep(BaseStep):
    step_name = "description"

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        research = self.get_previous_step_data("research")
        script = self.get_previous_step_data("script")
        teleprompter = self.get_previous_step_data("teleprompter")

        # Extract blocks from script (prefer "blocks", fallback to "scenes")
        blocks = script.get("blocks", script.get("scenes", []))

        # Calculate timestamps from block durations
        calculated_timestamps = _calculate_timestamps(blocks)
        timestamps_info = "\n".join(
            f"  {ts['time']} — {ts['label']}" for ts in calculated_timestamps
        )

        # Build block summary for context
        blocks_summary = []
        for b in blocks:
            num = b.get("block_number", b.get("scene_number", "?"))
            name = b.get("name", "—")
            btype = b.get("block_type", "")
            dur = b.get("duration_seconds", 0)
            points = b.get("talking_points", [])
            points_str = "; ".join(points[:3]) if points else ""
            blocks_summary.append(
                f"  Блок {num} [{btype}] «{name}» ({dur}s): {points_str}"
            )
        blocks_info = "\n".join(blocks_summary)

        # Get research SEO data
        seo_keywords = research.get("keywords", [])
        topic_analysis = research.get("topic_analysis", {})
        target_audience = research.get("target_audience", {})

        # Teleprompter word count for accurate video length
        word_count = teleprompter.get("total_word_count", 0)
        est_minutes = teleprompter.get("estimated_read_time_minutes",
                                       script.get("total_duration_minutes", 10))

        prompt = f"""Напиши SEO-оптимизированное описание для YouTube-видео.

Заголовок: {content_plan.get('title', self.state.topic)}
Тема: {self.state.topic}
Оценочная длина видео: ~{est_minutes} минут ({word_count} слов текста)

SEO-ключевые слова из исследования:
{json.dumps(seo_keywords, ensure_ascii=False)}

Целевая аудитория:
{json.dumps(target_audience, ensure_ascii=False, indent=2) if target_audience else 'не указана'}

Анализ темы:
{json.dumps(topic_analysis, ensure_ascii=False, indent=2) if topic_analysis else 'не указан'}

Контент-план:
- Хук: {content_plan.get('hook', '')}
- CTA: {content_plan.get('cta', '')}
- Теги из плана: {json.dumps(content_plan.get('tags', []), ensure_ascii=False)}
- Retention-хуки: {json.dumps(content_plan.get('retention_hooks', []), ensure_ascii=False)}

Структура видео (блоки сценария):
{blocks_info}

Рассчитанные таймкоды (используй их как основу, можно скорректировать названия):
{timestamps_info}

ТРЕБОВАНИЯ:
1. Первые 2 строки — САМЫЕ ВАЖНЫЕ (видны до "Ещё"), с главным ключом
2. Таймкоды для КАЖДОГО блока — точные, с привлекательными названиями
3. 10-15 SEO ключевых слов естественно вписанных в текст body
4. 15-25 тегов (от широких к узким)
5. 3-5 хэштегов (первые 3 появятся НАД заголовком)
6. Призыв к действию: подписка, лайк, вопрос для комментариев
7. Ссылки-заглушки (Telegram, соцсети, ресурсы)
8. ЕДИНЫЙ формат — этот шаблон будет использоваться для ВСЕХ видео канала
9. Язык: русский"""

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

        # Ensure timestamps exist (use calculated if Claude didn't provide)
        if not result.get("timestamps") and calculated_timestamps:
            result["timestamps"] = calculated_timestamps

        # Format and save the complete description file
        description_text = _format_description_file(result)
        desc_file = self.state.project_dir / "description.txt"
        with open(desc_file, "w") as f:
            f.write(description_text)
        result["description_file"] = str(desc_file)
        result["description_full_text"] = description_text

        # Log summary
        ts_count = len(result.get("timestamps", []))
        kw_count = len(result.get("keywords", []))
        tag_count = len(result.get("tags", []))
        ht_count = len(result.get("hashtags", []))
        logger.info(
            f"Description generated: {ts_count} timestamps, "
            f"{kw_count} keywords, {tag_count} tags, {ht_count} hashtags"
        )

        return result
