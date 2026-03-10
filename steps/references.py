"""Step 3: Cover references — collect competitor thumbnails and analyze design patterns."""

import json
import logging
from pathlib import Path

import requests

from steps.base import BaseStep

logger = logging.getLogger(__name__)

# How many competitor videos to fetch per search query
MAX_RESULTS_PER_QUERY = 10

# Minimum references to collect
MIN_REFERENCES = 5

ANALYSIS_SYSTEM_PROMPT = """Ты — эксперт по дизайну YouTube-обложек (thumbnails).
Проанализируй собранные обложки конкурентов и выяви паттерны успешного дизайна.

Ответ ВСЕГДА в формате JSON (без markdown-блоков):
{
  "pattern_analysis": {
    "fonts": {
      "dominant_styles": ["стиль 1", "стиль 2"],
      "text_length": "типичное кол-во слов на обложке",
      "text_position": "где обычно размещён текст",
      "text_size": "крупный/средний",
      "capitalization": "ВСЕ ЗАГЛАВНЫЕ / обычный"
    },
    "colors": {
      "dominant_palettes": [["#hex1", "#hex2"], ["#hex3", "#hex4"]],
      "contrast_level": "высокий/средний/низкий",
      "background_type": "фото/градиент/однотон/сцена",
      "accent_usage": "как используются акцентные цвета"
    },
    "composition": {
      "layout_patterns": ["паттерн 1", "паттерн 2"],
      "rule_of_thirds": true,
      "negative_space": "много/мало/умеренно",
      "visual_hierarchy": "описание иерархии элементов"
    },
    "faces_and_emotion": {
      "face_present": "всегда/часто/иногда/редко",
      "typical_expressions": ["эмоция 1", "эмоция 2"],
      "face_size": "крупный план/средний/мелкий",
      "face_position": "где обычно лицо"
    },
    "clickbait_elements": ["элемент 1", "элемент 2", "элемент 3"]
  },
  "top_references": [
    {
      "video_title": "название",
      "channel": "канал",
      "views": 0,
      "thumbnail_url": "url",
      "why_it_works": "почему эта обложка работает",
      "design_elements": ["элемент 1", "элемент 2"]
    }
  ],
  "design_recommendations": [
    "рекомендация 1",
    "рекомендация 2",
    "рекомендация 3"
  ],
  "concepts": [
    {
      "name": "Концепт A",
      "description": "описание концепта на основе анализа",
      "text_overlay": "Текст на обложке (2-4 слова, КРУПНЫЙ)",
      "background": "описание фона",
      "colors": ["#hex1", "#hex2", "#hex3"],
      "emotion": "какую эмоцию вызывает",
      "face_expression": "выражение лица (если есть лицо)",
      "layout": "описание расположения элементов",
      "contrast_trick": "как привлечь внимание",
      "inspired_by": "какой референс вдохновил"
    }
  ],
  "recommended": "название лучшего концепта",
  "a_b_test_pairs": ["Концепт A", "Концепт B"]
}"""

CONCEPTS_SYSTEM_PROMPT = """Ты — дизайнер YouTube-обложек (thumbnails).
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


def _download_thumbnail(url: str, output_path: Path) -> bool:
    """Download a thumbnail image from URL. Returns True on success."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        logger.warning(f"Failed to download thumbnail {url}: {e}")
        return False


class ReferencesStep(BaseStep):
    step_name = "references"

    def _collect_competitor_thumbnails(self, topic: str, title: str) -> list[dict]:
        """Search YouTube for competitor videos and collect their thumbnails."""
        try:
            from youtube_api import YouTubeAPI
            yt = YouTubeAPI()
        except Exception as e:
            logger.warning(f"YouTube API not available, skipping live search: {e}")
            return []

        references = []
        seen_ids = set()

        # Search with multiple queries for broader coverage
        queries = [
            title,
            topic,
            f"{topic} tutorial",
        ]

        for query in queries:
            if len(references) >= MAX_RESULTS_PER_QUERY:
                break
            try:
                # Search by relevance
                videos = yt.search_videos(query, max_results=MAX_RESULTS_PER_QUERY, order="relevance")
                for v in videos:
                    vid = v["video_id"]
                    if vid in seen_ids:
                        continue
                    seen_ids.add(vid)

                    # Get view stats
                    try:
                        stats = yt.get_video_stats(vid)
                        v["views"] = stats.get("views", 0)
                        v["likes"] = stats.get("likes", 0)
                    except Exception:
                        v["views"] = 0
                        v["likes"] = 0

                    references.append(v)

                # Also search by view count for top performers
                top_videos = yt.search_videos(query, max_results=5, order="viewCount")
                for v in top_videos:
                    vid = v["video_id"]
                    if vid in seen_ids:
                        continue
                    seen_ids.add(vid)
                    try:
                        stats = yt.get_video_stats(vid)
                        v["views"] = stats.get("views", 0)
                        v["likes"] = stats.get("likes", 0)
                    except Exception:
                        v["views"] = 0
                        v["likes"] = 0
                    references.append(v)

            except Exception as e:
                logger.warning(f"Search failed for query '{query}': {e}")
                continue

        # Sort by views descending, take top references
        references.sort(key=lambda x: x.get("views", 0), reverse=True)
        return references[:MAX_RESULTS_PER_QUERY]

    def _download_reference_thumbnails(self, references: list[dict], refs_dir: Path) -> list[dict]:
        """Download thumbnail images for each reference. Adds local_path to each ref."""
        refs_dir.mkdir(parents=True, exist_ok=True)
        for i, ref in enumerate(references):
            thumb_url = ref.get("thumbnail_url", "")
            if not thumb_url:
                continue
            filename = f"ref_{i + 1}_{ref.get('video_id', 'unknown')}.jpg"
            local_path = refs_dir / filename
            if _download_thumbnail(thumb_url, local_path):
                ref["local_path"] = str(local_path)
                logger.info(f"Downloaded reference thumbnail: {filename}")
            else:
                ref["local_path"] = ""
        return references

    def _analyze_with_claude(self, topic: str, title: str, references: list[dict]) -> dict:
        """Use Claude to analyze competitor thumbnails and generate concepts."""
        ref_descriptions = []
        for i, ref in enumerate(references):
            ref_descriptions.append(
                f"{i + 1}. \"{ref.get('title', '?')}\" "
                f"(канал: {ref.get('channel', '?')}, "
                f"просмотры: {ref.get('views', '?'):,}, "
                f"лайки: {ref.get('likes', '?'):,})\n"
                f"   Обложка: {ref.get('thumbnail_url', 'нет')}"
            )

        prompt = f"""Проанализируй обложки (thumbnails) конкурентов для YouTube-видео.

Заголовок нашего видео: {title}
Тема: {topic}

Найденные конкуренты (топ по просмотрам):
{chr(10).join(ref_descriptions)}

На основе заголовков, каналов и популярности этих видео:
1. Проанализируй паттерны успешных обложек в этой нише (шрифты, цвета, композиция, лица, кликбейт-элементы)
2. Выбери 5-7 лучших референсов и объясни почему они работают
3. Дай рекомендации по дизайну обложки для нашего видео
4. Создай 3 концепта обложек, вдохновлённых лучшими практиками конкурентов

Важно: анализируй типичные паттерны обложек в этой нише на YouTube."""

        response = self.ask_claude(ANALYSIS_SYSTEM_PROMPT, prompt)

        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response[start:end])
            else:
                result = {"raw_response": response}

        return result

    def _generate_concepts_only(self, topic: str, title: str) -> dict:
        """Fallback: generate concepts without live YouTube data."""
        prompt = f"""Создай 3 концепта обложек для YouTube-видео.

Заголовок: {title}
Тема: {topic}
Целевая аудитория: видеоформат 10+ минут

Требования:
- Крупный текст (2-4 слова максимум на обложке)
- Яркие, контрастные цвета
- Эмоция (удивление, любопытство, шок)
- Формат 1280x720
- Должно быть понятно о чём видео даже без заголовка
- Кликбейт-стиль, но не обманчивый"""

        response = self.ask_claude(CONCEPTS_SYSTEM_PROMPT, prompt)

        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response[start:end])
            else:
                result = {"raw_response": response}

        return result

    def execute(self) -> dict:
        content_plan = self.get_previous_step_data("content_plan")
        title = content_plan.get("title", self.state.topic)
        topic = self.state.topic

        # Directory for downloaded reference thumbnails
        refs_dir = self.state.project_dir / "references"

        # Step 1: Collect competitor thumbnails from YouTube
        logger.info(f"Searching YouTube for competitor thumbnails: {topic}")
        references = self._collect_competitor_thumbnails(topic, title)

        if references:
            # Step 2: Download thumbnail images locally
            logger.info(f"Downloading {len(references)} reference thumbnails...")
            references = self._download_reference_thumbnails(references, refs_dir)

            # Step 3: Analyze patterns with Claude
            logger.info("Analyzing competitor thumbnail patterns...")
            analysis = self._analyze_with_claude(topic, title, references)

            # Attach raw reference data
            analysis["competitor_videos"] = [
                {
                    "video_id": r.get("video_id"),
                    "title": r.get("title"),
                    "channel": r.get("channel"),
                    "views": r.get("views", 0),
                    "likes": r.get("likes", 0),
                    "thumbnail_url": r.get("thumbnail_url", ""),
                    "local_path": r.get("local_path", ""),
                    "url": r.get("url", ""),
                }
                for r in references
            ]
            analysis["references_dir"] = str(refs_dir)
            analysis["total_references"] = len(references)

            logger.info(
                f"Collected {len(references)} references, "
                f"created {len(analysis.get('concepts', []))} concepts"
            )
        else:
            # Fallback: no YouTube API access, generate concepts only
            logger.info("No YouTube data available, generating concepts from knowledge...")
            analysis = self._generate_concepts_only(topic, title)
            analysis["competitor_videos"] = []
            analysis["references_dir"] = str(refs_dir)
            analysis["total_references"] = 0
            logger.info(f"Created {len(analysis.get('concepts', []))} concepts (no live references)")

        # Save analysis summary to file
        summary_file = refs_dir / "analysis.json"
        refs_dir.mkdir(parents=True, exist_ok=True)
        with open(summary_file, "w") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
        analysis["analysis_file"] = str(summary_file)

        return analysis
