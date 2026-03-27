"""Step 2: Content plan — create a structured content plan based on real research data."""

import json
import logging

from steps.base import BaseStep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — YouTube-стратег и контент-планировщик.
Создай детальный контент-план для длинного YouTube-видео (10+ минут).

Тебе даны РЕАЛЬНЫЕ данные исследования с YouTube — используй их для создания плана,
который ПЕРЕПЛЮНЕТ конкурентов.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "title": "финальный кликбейтный заголовок видео",
  "subtitle": "подзаголовок / альтернативный заголовок для A/B теста",
  "hook": "хук первых 30 секунд — чем зацепить зрителя (конкретный сценарий)",
  "angle": "угол подачи — чем наше видео отличается от конкурентов",
  "target_length_minutes": 12,
  "structure": [
    {
      "section": "название раздела",
      "duration_minutes": 2,
      "key_points": ["пункт 1", "пункт 2"],
      "visual_notes": "заметки по визуалу",
      "retention_hook": "чем удержать зрителя в этом блоке"
    }
  ],
  "cta": "призыв к действию",
  "retention_hooks": ["хук удержания 1", "хук удержания 2"],
  "b_roll_ideas": ["идея для перебивки 1", "идея для перебивки 2"],
  "tags": ["тег1", "тег2"],
  "thumbnail_text": "2-4 слова для обложки (КРУПНЫЙ текст)",
  "thumbnail_emotion": "какую эмоцию должна вызывать обложка",
  "why_this_will_work": "почему этот план сработает лучше конкурентов"
}"""


class ContentPlanStep(BaseStep):
    step_name = "content_plan"

    def execute(self) -> dict:
        research = self.get_previous_step_data("research")

        # Extract rich data from new research step
        hot_angle = research.get("hot_angle", "")
        recommended_approach = research.get("recommended_approach", "")
        best_video = research.get("best_performing_video", {})
        competitors = research.get("competitors", [])
        thumbnail_analysis = research.get("thumbnail_analysis", "")

        # Top 5 competitors summary
        top_competitors = ""
        for i, c in enumerate(competitors[:5]):
            top_competitors += (
                f"\n  {i+1}. {c.get('channel', '?')} — "
                f"\"{c.get('video_title', '?')}\" "
                f"({c.get('views', 0):,} просмотров)\n"
                f"     Сильные стороны: {c.get('strengths', '-')}\n"
                f"     Слабые стороны: {c.get('weaknesses', '-')}"
            )

        prompt = f"""Создай контент-план для YouTube-видео.

Тема: {self.state.topic}

=== РЕАЛЬНЫЕ ДАННЫЕ ИССЛЕДОВАНИЯ ===

Самый горячий угол: {hot_angle}

Рекомендованный подход: {recommended_approach}

Лучшее видео конкурентов:
  "{best_video.get('title', '?')}" — {best_video.get('views', 0):,} просмотров
  Почему оно работает: {best_video.get('why_it_works', '?')}

Топ-5 конкурентов:{top_competitors}

Ключевые слова: {json.dumps(research.get('keywords', []), ensure_ascii=False)}
Рекомендованные заголовки: {json.dumps(research.get('recommended_titles', []), ensure_ascii=False)}
Целевая аудитория: {research.get('target_audience', 'не определена')}
Незакрытые ниши: {json.dumps(research.get('content_gaps', []), ensure_ascii=False)}
Трендовые подходы: {json.dumps(research.get('trending_angles', []), ensure_ascii=False)}
Анализ обложек конкурентов: {thumbnail_analysis}

Средние просмотры: RU {research.get('_avg_views_ru', 0):,} / EN {research.get('_avg_views_en', 0):,}

=== ЗАДАЧА ===

Создай план видео, которое:
1. Использует ЛУЧШИЙ УГОЛ из исследования (hot_angle)
2. Закрывает content gaps конкурентов
3. Имеет более сильный хук чем у лучшего видео конкурентов
4. Минимум 10 минут хронометража
5. Retention-хуки каждые 2-3 минуты
6. Чёткая структура с таймингами
7. CTA (подписка, лайк, комментарий)
8. Предложи текст для обложки (2-4 слова, КРУПНЫЙ, кликбейтный)"""

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

        logger.info(f"Content plan created: {result.get('title', 'untitled')}")
        return result
