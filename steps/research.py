"""Step 1: Topic research — real YouTube API search + Claude analysis.

Multi-query search strategy:
- Claude generates 5-7 query variations from the topic
- Each query searched with order=viewCount + publishedAfter (last year)
- Deduplication by video_id
- Downloads thumbnails, saves raw data
- Claude analyzes real data and finds the best trending angle
"""

import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from PIL import Image

from config import YOUTUBE_API_KEY
from steps.base import BaseStep

logger = logging.getLogger(__name__)

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _parse_duration_seconds(iso_duration: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    import re
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s


def _search_youtube(
    query: str,
    max_results: int = 10,
    language: str = "ru",
    order: str = "viewCount",
    published_after: str | None = None,
    video_duration: str = "medium",
) -> list[dict]:
    """Search YouTube and return list of video IDs + basic info.

    video_duration: "short" (<4 min), "medium" (4-20 min), "long" (>20 min), "any"
    """
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": order,
        "maxResults": max_results,
        "relevanceLanguage": language,
        "videoDuration": video_duration,
        "key": YOUTUBE_API_KEY,
    }
    if published_after:
        params["publishedAfter"] = published_after

    resp = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        results.append({
            "video_id": item["id"]["videoId"],
            "title": snippet.get("title", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "description": snippet.get("description", ""),
            "published_at": snippet.get("publishedAt", ""),
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "search_query": query,
            "search_lang": language,
        })
    return results


def _get_video_details(video_ids: list[str]) -> dict[str, dict]:
    """Fetch detailed statistics and content details for a list of video IDs."""
    if not video_ids:
        return {}

    # YouTube API accepts max 50 IDs per request
    all_details = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        params = {
            "part": "statistics,contentDetails,snippet",
            "id": ",".join(batch),
            "key": YOUTUBE_API_KEY,
        }
        resp = requests.get(YOUTUBE_VIDEOS_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            vid = item["id"]
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})
            snippet = item.get("snippet", {})
            all_details[vid] = {
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "duration": content.get("duration", ""),
                "tags": snippet.get("tags", []),
                "category_id": snippet.get("categoryId", ""),
                "default_language": snippet.get("defaultLanguage", ""),
                "default_audio_language": snippet.get("defaultAudioLanguage", ""),
            }
    return all_details


def _download_thumbnail(url: str, output_path: Path) -> bool:
    """Download a thumbnail image and save it."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        img.save(output_path, "JPEG", quality=90)
        return True
    except Exception as e:
        logger.warning(f"Failed to download thumbnail {url}: {e}")
        return False


QUERY_GEN_SYSTEM = """Ты — YouTube SEO-эксперт. Пользователь дает тему для видео.
Сгенерируй 6 поисковых запросов для YouTube, которые помогут найти самые популярные
и стрельнувшие видео по этой теме за последний год.

Запросы должны покрывать:
- Точный запрос по теме
- Синонимы и альтернативные формулировки
- Более широкий контекст (смежные темы)
- Провокационные/кликбейтные формулировки (как реально ищут люди)
- Вопросительные формы ("как ...", "почему ...", "зачем ...")

Ответь СТРОГО JSON-массивом из 6 строк, без markdown:
["запрос 1", "запрос 2", "запрос 3", "запрос 4", "запрос 5", "запрос 6"]"""


ANALYSIS_SYSTEM_PROMPT = """Ты — YouTube SEO-аналитик.
Тебе даны РЕАЛЬНЫЕ данные о видео с YouTube по заданной теме.
Все видео найдены по нескольким поисковым запросам, отсортированы по просмотрам, за последний год.

Твоя задача — найти СТРЕЛЬНУВШУЮ тему/угол, который набрал больше всего просмотров,
и дать рекомендации как сделать видео ещё лучше.

Ответ СТРОГО в формате JSON (без markdown-блоков):
{
  "topic_analysis": "подробный анализ — какие подтемы стреляют, какие нет",
  "target_audience": "описание целевой аудитории на основе реальных данных",
  "hot_angle": "самый горячий угол/подтема которая стреляет прямо сейчас",
  "competitors": [
    {
      "channel": "название канала",
      "video_title": "название видео",
      "video_id": "ID видео",
      "views": 123456,
      "strengths": "что хорошо — конкретно",
      "weaknesses": "что можно улучшить — конкретно"
    }
  ],
  "trending_angles": ["стрельнувший угол 1", "угол 2", "угол 3"],
  "keywords": ["ключевое слово 1", "ключевое слово 2"],
  "recommended_titles": [
    "Заголовок 1 (объяснение почему зайдёт)",
    "Заголовок 2 (объяснение)",
    "Заголовок 3 (объяснение)",
    "Заголовок 4 (объяснение)",
    "Заголовок 5 (объяснение)"
  ],
  "thumbnail_analysis": "анализ обложек топовых видео — общие паттерны, цвета, текст, лица",
  "content_gaps": ["что упускают конкуренты 1", "пробел 2"],
  "recommended_duration_minutes": 12,
  "top_tags": ["тег1", "тег2", "тег3", "тег4", "тег5"],
  "avg_views_ru": 0,
  "avg_views_en": 0,
  "best_performing_video": {
    "title": "",
    "video_id": "",
    "views": 0,
    "why_it_works": "ПОДРОБНО — почему именно это видео набрало больше всего"
  },
  "recommended_approach": "конкретная рекомендация — какое видео снимать, с каким углом, заголовком"
}"""


class ResearchStep(BaseStep):
    step_name = "research"

    def _generate_queries(self, topic: str) -> list[str]:
        """Use Claude to generate multiple search query variations."""
        response = self.ask_claude(
            QUERY_GEN_SYSTEM,
            f"Тема: {topic}"
        )
        try:
            queries = json.loads(response)
            if isinstance(queries, list):
                return [str(q) for q in queries[:6]]
        except json.JSONDecodeError:
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                queries = json.loads(response[start:end])
                return [str(q) for q in queries[:6]]
        # Fallback: just use the topic itself
        return [topic]

    def execute(self) -> dict:
        topic = self.state.topic
        if not topic:
            raise ValueError("Topic not set. Set state.topic before running research.")

        if not YOUTUBE_API_KEY:
            raise ValueError("YOUTUBE_API_KEY not configured in .env")

        references_dir = self.state.project_dir / "references"
        references_dir.mkdir(parents=True, exist_ok=True)

        # --- Date filter: last 12 months ---
        one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")

        # --- Generate query variations ---
        logger.info(f"Generating search queries for topic: {topic}")
        queries = self._generate_queries(topic)
        logger.info(f"Generated {len(queries)} queries: {queries}")

        # --- Multi-query search ---
        seen_ids = set()
        ru_videos = []
        en_videos = []

        # Search with both "medium" (4-20 min) and "long" (20+ min) filters
        # to exclude shorts and short clips
        for q in queries:
            for duration_filter in ("medium", "long"):
                # RU search — by viewCount, last year, 4+ min
                logger.info(f"Searching RU (viewCount, {duration_filter}): {q}")
                results = _search_youtube(
                    q, max_results=10, language="ru",
                    order="viewCount", published_after=one_year_ago,
                    video_duration=duration_filter,
                )
                for v in results:
                    if v["video_id"] not in seen_ids:
                        seen_ids.add(v["video_id"])
                        ru_videos.append(v)

            # EN search — by viewCount, last year, medium only (top long-form)
            logger.info(f"Searching EN (viewCount, medium): {q}")
            results = _search_youtube(
                q, max_results=5, language="en",
                order="viewCount", published_after=one_year_ago,
                video_duration="medium",
            )
            for v in results:
                if v["video_id"] not in seen_ids:
                    seen_ids.add(v["video_id"])
                    en_videos.append(v)

        # Also do one relevance search to catch fresh trending content
        logger.info(f"Searching RU (relevance, fresh, medium): {topic}")
        fresh_results = _search_youtube(
            topic, max_results=10, language="ru",
            order="relevance", published_after=one_year_ago,
            video_duration="medium",
        )
        for v in fresh_results:
            if v["video_id"] not in seen_ids:
                seen_ids.add(v["video_id"])
                ru_videos.append(v)

        logger.info(f"Total unique (before duration filter): {len(ru_videos)} RU + {len(en_videos)} EN")

        all_videos = ru_videos + en_videos

        # --- Get detailed stats ---
        video_ids = [v["video_id"] for v in all_videos]
        logger.info(f"Fetching details for {len(video_ids)} videos...")
        details = _get_video_details(video_ids)

        # Merge details into video dicts
        for v in all_videos:
            vid = v["video_id"]
            if vid in details:
                v.update(details[vid])

        # --- Filter: only 10+ minute videos ---
        MIN_DURATION_SEC = 600  # 10 minutes
        before_count = len(all_videos)
        all_videos = [
            v for v in all_videos
            if _parse_duration_seconds(v.get("duration", "")) >= MIN_DURATION_SEC
        ]
        ru_videos = [v for v in ru_videos if v in all_videos]
        en_videos = [v for v in en_videos if v in all_videos]
        logger.info(
            f"Duration filter (10+ min): {before_count} → {len(all_videos)} videos "
            f"({before_count - len(all_videos)} shorts/clips removed)"
        )

        # --- Download thumbnails (top 30 by views) ---
        sorted_all = sorted(all_videos, key=lambda x: x.get("view_count", 0), reverse=True)
        top_for_thumbs = sorted_all[:30]

        logger.info(f"Downloading thumbnails for top {len(top_for_thumbs)} videos...")
        for i, v in enumerate(top_for_thumbs):
            url = v.get("thumbnail_url", "")
            if url:
                views_k = v.get("view_count", 0) // 1000
                filename = f"{views_k}k_{v['search_lang']}_{v['video_id']}.jpg"
                thumb_path = references_dir / filename
                if _download_thumbnail(url, thumb_path):
                    v["thumbnail_local"] = str(thumb_path)
                    logger.info(f"  [{i+1}] {views_k}k views — {v['title'][:50]}")

        # --- Save raw data ---
        raw_path = references_dir / "videos_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(sorted_all, f, ensure_ascii=False, indent=2)
        logger.info(f"Raw data saved: {raw_path} ({len(sorted_all)} videos)")

        # --- Build summary for Claude (top 40 by views) ---
        ru_summary = []
        en_summary = []
        for v in sorted_all[:40]:
            entry = {
                "video_id": v["video_id"],
                "title": v["title"],
                "channel": v["channel_title"],
                "views": v.get("view_count", 0),
                "likes": v.get("like_count", 0),
                "comments": v.get("comment_count", 0),
                "duration": v.get("duration", ""),
                "tags": v.get("tags", [])[:10],
                "published": v.get("published_at", ""),
                "found_by_query": v.get("search_query", ""),
            }
            if v in ru_videos:
                ru_summary.append(entry)
            else:
                en_summary.append(entry)

        # Stats
        ru_views = [v.get("view_count", 0) for v in ru_videos if v.get("view_count")]
        en_views = [v.get("view_count", 0) for v in en_videos if v.get("view_count")]
        avg_ru = sum(ru_views) // len(ru_views) if ru_views else 0
        avg_en = sum(en_views) // len(en_views) if en_views else 0

        prompt = f"""Проанализируй РЕАЛЬНЫЕ данные YouTube по теме: {topic}

Поисковые запросы, которые использовались: {json.dumps(queries, ensure_ascii=False)}
Период: последние 12 месяцев
Сортировка: по количеству просмотров (самые популярные)

Всего найдено: {len(ru_videos)} RU видео, {len(en_videos)} EN видео
Средние просмотры: RU {avg_ru:,} / EN {avg_en:,}

Топ русскоязычных видео (по просмотрам):
{json.dumps(ru_summary, ensure_ascii=False, indent=2)}

Топ англоязычных видео (для сравнения):
{json.dumps(en_summary, ensure_ascii=False, indent=2)}

ЗАДАЧА:
1. Найди САМУЮ СТРЕЛЬНУВШУЮ подтему/угол — что набирает больше всего просмотров
2. Проанализируй ТОП-5 видео — почему именно они выстрелили
3. Какие заголовки работают лучше всего (конкретные паттерны)
4. Какие теги/ключевые слова у топовых видео
5. Что упускают конкуренты — где можно сделать лучше
6. Предложи 5 заголовков которые переплюнут конкурентов
7. Дай КОНКРЕТНУЮ рекомендацию — какое видео снимать, с каким углом
8. Анализ обложек — общие паттерны у топовых видео"""

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

        # Attach metadata
        result["_search_queries"] = queries
        result["_videos_found_ru"] = len(ru_videos)
        result["_videos_found_en"] = len(en_videos)
        result["_total_unique"] = len(all_videos)
        result["_avg_views_ru"] = avg_ru
        result["_avg_views_en"] = avg_en
        result["_raw_data_path"] = str(raw_path)
        result["_references_dir"] = str(references_dir)
        result["_top_video"] = sorted_all[0] if sorted_all else {}
        result["_period"] = "last 12 months"

        logger.info(
            f"Research completed: {len(ru_videos)} RU + {len(en_videos)} EN videos "
            f"({len(all_videos)} unique), top views: {sorted_all[0].get('view_count', 0):,} "
            f"— {sorted_all[0].get('title', '')[:60]}" if sorted_all else "no videos"
        )
        return result
