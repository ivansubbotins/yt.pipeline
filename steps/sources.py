"""Step: Sources — fetch external content, extract facts, quotes, statistics.

NotebookLM-style: user provides URLs or text → we scrape → Claude extracts
structured data → downstream steps use it for content generation.
"""

import json
import logging
import re

import requests
from bs4 import BeautifulSoup

from steps.base import BaseStep

logger = logging.getLogger(__name__)

# Headers to mimic a browser
FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

MAX_WORDS_PER_SOURCE = 5000
MAX_TOTAL_WORDS = 15000


SCRAPECREATORS_API_KEY = ""
try:
    import os as _os
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
    SCRAPECREATORS_API_KEY = _os.getenv("SCRAPECREATORS_API_KEY", "")
except Exception:
    pass


def _fetch_transcript_scrapecreators(video_id: str, url: str) -> dict | None:
    """Fallback: fetch transcript via ScrapeCreators API."""
    if not SCRAPECREATORS_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://api.scrapecreators.com/v1/youtube/video/transcript",
            headers={"x-api-key": SCRAPECREATORS_API_KEY},
            params={"url": url, "language": "ru"},
            timeout=30,
        )
        if resp.status_code != 200:
            # Try without language filter
            resp = requests.get(
                "https://api.scrapecreators.com/v1/youtube/video/transcript",
                headers={"x-api-key": SCRAPECREATORS_API_KEY},
                params={"url": url},
                timeout=30,
            )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("transcript_only_text", "")
            if text:
                logger.info(f"ScrapeCreators transcript: {len(text.split())} words for {video_id}")
                return {"text": text, "source": "scrapecreators"}
        return None
    except Exception as e:
        logger.warning(f"ScrapeCreators error: {e}")
        return None


def extract_youtube_transcript(url: str) -> dict:
    """Extract transcript from a YouTube video. Free first, ScrapeCreators as fallback."""
    import re
    from youtube_transcript_api import YouTubeTranscriptApi

    # Extract video ID from URL
    match = re.search(r'(?:v=|youtu\.be/|/v/|/embed/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        return {"error": "Не удалось извлечь video ID из URL", "text": "", "title": ""}

    video_id = match.group(1)

    try:
        ytt = YouTubeTranscriptApi()
        try:
            segments = ytt.fetch(video_id, languages=['ru', 'en'])
        except Exception:
            segments = ytt.fetch(video_id)

        # Combine all text segments
        text = " ".join(seg.text if hasattr(seg, 'text') else seg.get('text', '') for seg in segments)
        # Clean up
        text = re.sub(r'\[.*?\]', '', text)  # Remove [Music], [Applause] etc
        text = re.sub(r'\s+', ' ', text).strip()

        last = segments[-1] if segments else None
        if last:
            start = last.start if hasattr(last, 'start') else last.get('start', 0)
            dur = last.duration if hasattr(last, 'duration') else last.get('duration', 0)
            duration_sec = int(start + dur)
        else:
            duration_sec = 0
        duration_min = round(duration_sec / 60, 1)

        # Get video title via simple request
        title = ""
        try:
            resp = requests.get(f"https://www.youtube.com/watch?v={video_id}", headers=FETCH_HEADERS, timeout=10)
            title_match = re.search(r'<title>(.*?)</title>', resp.text)
            if title_match:
                title = title_match.group(1).replace(" - YouTube", "").strip()
        except Exception:
            pass

        word_count = len(text.split())
        logger.info(f"YouTube transcript extracted: {video_id} — {word_count} words, {duration_min} min")

        return {
            "text": text,
            "title": title or f"YouTube: {video_id}",
            "video_id": video_id,
            "word_count": word_count,
            "duration_minutes": duration_min,
        }
    except Exception as e:
        logger.warning(f"YouTube transcript error for {video_id}: {e}")
        # Fallback to ScrapeCreators
        logger.info(f"Trying ScrapeCreators fallback for {video_id}...")
        sc_result = _fetch_transcript_scrapecreators(video_id, url)
        if sc_result and sc_result.get("text"):
            text = sc_result["text"]
            word_count = len(text.split())
            # Get title
            title = ""
            try:
                resp = requests.get(f"https://www.youtube.com/watch?v={video_id}", headers=FETCH_HEADERS, timeout=10)
                title_match = re.search(r'<title>(.*?)</title>', resp.text)
                if title_match:
                    title = title_match.group(1).replace(" - YouTube", "").strip()
            except Exception:
                pass
            return {
                "text": text,
                "title": title or f"YouTube: {video_id}",
                "video_id": video_id,
                "word_count": word_count,
                "duration_minutes": 0,
                "source": "scrapecreators",
            }
        return {"error": str(e), "text": "", "title": f"YouTube: {video_id}"}


def query_notebooklm(notebook_id: str, question: str) -> str:
    """Query a NotebookLM notebook via nlm CLI."""
    import subprocess, os
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            ["nlm", "notebook", "query", notebook_id, question, "--json", "--timeout", "300"],
            capture_output=True, timeout=360, encoding="utf-8", errors="replace", env=env,
        )
        if result.returncode == 0:
            return result.stdout
        else:
            logger.warning(f"NotebookLM query failed (exit {result.returncode}): {result.stderr[:200]}")
            return ""
    except subprocess.TimeoutExpired:
        logger.warning(f"NotebookLM query timed out for notebook {notebook_id}")
        return ""
    except Exception as e:
        logger.warning(f"NotebookLM query error: {e}")
        return ""


def get_notebook_sources(notebook_id: str) -> list[dict]:
    """List sources in a NotebookLM notebook."""
    import subprocess, os
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            ["nlm", "source", "list", notebook_id, "--json"],
            capture_output=True, timeout=60, encoding="utf-8", errors="replace", env=env,
        )
        if result.returncode == 0:
            import json as json_mod
            data = json_mod.loads(result.stdout)
            return data if isinstance(data, list) else data.get("sources", [])
        return []
    except Exception as e:
        logger.warning(f"NotebookLM sources list error: {e}")
        return []


def fetch_url(url: str) -> dict:
    """Fetch a URL and extract clean text content."""
    try:
        resp = requests.get(url, headers=FETCH_HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return {"error": str(e), "title": "", "text": "", "word_count": 0}

    soup = BeautifulSoup(html, "html.parser")

    # Extract title
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)

    # Remove unwanted elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside",
                              "iframe", "noscript", "form", "button", "svg"]):
        tag.decompose()

    # Try to find main content area
    main = soup.find("article") or soup.find("main") or soup.find(class_=re.compile(r"article|content|post|entry"))
    if main:
        text = main.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Clean up: remove excessive whitespace, empty lines
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = "\n".join(lines)

    # Limit to MAX_WORDS_PER_SOURCE
    words = text.split()
    if len(words) > MAX_WORDS_PER_SOURCE:
        text = " ".join(words[:MAX_WORDS_PER_SOURCE])

    return {
        "title": title,
        "text": text,
        "word_count": len(words),
        "url": url,
    }


ANALYSIS_SYSTEM_PROMPT = """Ты — аналитик-исследователь. Тебе даны тексты из внешних источников (статьи, заметки).

Извлеки из них КОНКРЕТНЫЕ данные для использования в YouTube-видео.

Ответ СТРОГО в формате JSON (без markdown-блоков):
{
  "facts": [
    {"text": "конкретный факт", "source": "название источника"}
  ],
  "quotes": [
    {"text": "цитата эксперта", "author": "имя автора/эксперта", "source": "источник"}
  ],
  "statistics": [
    {"metric": "число/процент", "context": "что это число означает", "source": "источник"}
  ],
  "key_insights": [
    "инсайт 1 — что важного для зрителя",
    "инсайт 2"
  ],
  "counterarguments": [
    "контраргумент или альтернативная точка зрения"
  ],
  "summary": "Краткое резюме всех источников (2-3 предложения) — главная мысль"
}

Требования:
- Извлекай ТОЛЬКО реальные данные из текстов, НЕ выдумывай
- Факты должны быть КОНКРЕТНЫЕ (с цифрами, именами, датами)
- Цитаты — дословные из текста
- Статистика — точные числа с контекстом
- Инсайты — что зритель должен понять
- Контраргументы — для баланса и глубины"""


class SourcesStep(BaseStep):
    step_name = "sources"

    def _auto_populate_from_research(self, sources_file):
        """Auto-populate sources with top YouTube videos from Research step."""
        research = self.get_previous_step_data("research")
        if not research:
            return

        # Load existing sources
        sources_data = {"items": [], "extracted": None}
        if sources_file.exists():
            with open(sources_file, "r", encoding="utf-8") as f:
                sources_data = json.load(f)

        existing_urls = {item.get("url", "") for item in sources_data.get("items", [])}

        # Add breakthrough videos
        breakthroughs = research.get("_breakthroughs", [])
        competitors = research.get("competitors", [])

        added = 0
        for video in breakthroughs[:5]:  # Top 5 breakthroughs
            vid = video.get("video_id", "")
            if not vid:
                continue
            url = f"https://youtube.com/watch?v={vid}"
            if url in existing_urls:
                continue
            sources_data["items"].append({
                "id": str(int(__import__('time').time() * 1000) + added),
                "type": "youtube",
                "url": url,
                "title": video.get("title", ""),
                "status": "pending",
                "auto_added": True,
                "added_at": __import__('datetime').datetime.now().isoformat(),
                "views": video.get("views", 0),
                "subscribers": video.get("subscribers", 0),
                "breakthrough_score": video.get("breakthrough_score", 0),
            })
            existing_urls.add(url)
            added += 1

        # Add top competitors (up to 3)
        for comp in competitors[:3]:
            vid = comp.get("video_id", "")
            if not vid:
                continue
            url = f"https://youtube.com/watch?v={vid}"
            if url in existing_urls:
                continue
            sources_data["items"].append({
                "id": str(int(__import__('time').time() * 1000) + added),
                "type": "youtube",
                "url": url,
                "title": comp.get("video_title", ""),
                "status": "pending",
                "auto_added": True,
                "added_at": __import__('datetime').datetime.now().isoformat(),
            })
            existing_urls.add(url)
            added += 1

        if added > 0:
            sources_file.parent.mkdir(parents=True, exist_ok=True)
            with open(sources_file, "w", encoding="utf-8") as f:
                json.dump(sources_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Auto-populated {added} YouTube videos from Research")

    def execute(self) -> dict:
        # Auto-populate from research if no sources yet
        sources_file = self.state.project_dir / "sources.json"
        self._auto_populate_from_research(sources_file)

        # Read sources
        if not sources_file.exists():
            logger.info("No sources.json found — skipping sources step")
            return {"skipped": True, "message": "Источники не добавлены"}

        with open(sources_file, "r", encoding="utf-8") as f:
            sources_data = json.load(f)

        items = sources_data.get("items", [])
        if not items:
            return {"skipped": True, "message": "Список источников пуст"}

        # Fetch and extract content from each source
        all_texts = []
        fetched_items = []
        total_words = 0

        for item in items:
            if item.get("type") == "url" and item.get("url"):
                logger.info(f"Fetching URL: {item['url']}")
                result = fetch_url(item["url"])
                if result.get("error"):
                    item["status"] = "error"
                    item["error"] = result["error"]
                    fetched_items.append(item)
                    continue
                item["status"] = "fetched"
                item["title"] = result["title"] or item.get("title", "")
                item["word_count"] = result["word_count"]
                item["text_preview"] = result["text"][:500]
                all_texts.append(f"=== ИСТОЧНИК: {item['title'] or item['url']} ===\n{result['text']}")
                total_words += result["word_count"]
                fetched_items.append(item)

            elif item.get("type") == "youtube" and item.get("url"):
                logger.info(f"Extracting YouTube transcript: {item['url']}")
                result = extract_youtube_transcript(item["url"])
                if result.get("error"):
                    item["status"] = "error"
                    item["error"] = result["error"]
                    fetched_items.append(item)
                    continue
                item["status"] = "fetched"
                item["title"] = result["title"]
                item["word_count"] = result["word_count"]
                item["duration_minutes"] = result.get("duration_minutes", 0)
                item["video_id"] = result.get("video_id", "")
                text = result["text"]
                # Truncate if needed
                words = text.split()
                if len(words) > MAX_WORDS_PER_SOURCE:
                    text = " ".join(words[:MAX_WORDS_PER_SOURCE])
                all_texts.append(f"=== ИСТОЧНИК: YouTube — {item['title']} ===\n{text}")
                total_words += result["word_count"]
                fetched_items.append(item)

            elif item.get("type") == "text" and item.get("content"):
                words = item["content"].split()
                item["status"] = "ready"
                item["word_count"] = len(words)
                content = " ".join(words[:MAX_WORDS_PER_SOURCE])
                all_texts.append(f"=== ИСТОЧНИК: {item.get('title', 'Заметки')} ===\n{content}")
                total_words += len(words)
                fetched_items.append(item)

            elif item.get("type") == "notebook" and item.get("notebook_id"):
                logger.info(f"Querying NotebookLM notebook: {item['notebook_id']}")
                # Pre-check auth
                auth_check = query_notebooklm(item["notebook_id"], "test")
                if "expired" in auth_check.lower() or "authentication" in auth_check.lower():
                    item["status"] = "auth_expired"
                    item["error"] = "Авторизация NotebookLM истекла. Запустите в терминале: nlm login"
                    fetched_items.append(item)
                    logger.warning("NotebookLM auth expired — skipping notebook queries")
                    continue
                # Query notebook for comprehensive extraction
                queries = [
                    "Извлеки ВСЕ ключевые факты, цифры, статистику и данные из всех источников. Отвечай подробно.",
                    "Найди ВСЕ цитаты экспертов, мнения и высказывания из источников. Приведи дословно.",
                    "Какие главные инсайты, выводы и практические рекомендации можно извлечь из этих источников?",
                ]
                nb_texts = []
                for q in queries:
                    response = query_notebooklm(item["notebook_id"], q)
                    if response:
                        try:
                            resp_data = json.loads(response)
                            # nlm returns {"value": {"answer": "..."}} or {"answer": "..."}
                            if "value" in resp_data and isinstance(resp_data["value"], dict):
                                answer = resp_data["value"].get("answer", "")
                            elif "answer" in resp_data:
                                answer = resp_data["answer"]
                            else:
                                answer = resp_data.get("text", str(resp_data))
                        except (json.JSONDecodeError, TypeError):
                            answer = response
                        if answer and "error" not in str(answer).lower()[:50]:
                            nb_texts.append(answer)
                            logger.info(f"  NotebookLM query {queries.index(q)+1}/3: got {len(answer)} chars")

                # Also get list of sources in notebook
                nb_sources = get_notebook_sources(item["notebook_id"])
                item["nb_sources_count"] = len(nb_sources)
                item["nb_source_titles"] = [s.get("title", "?") for s in nb_sources[:20]]

                if nb_texts:
                    combined_nb = "\n\n".join(nb_texts)
                    words_count = len(combined_nb.split())
                    item["status"] = "fetched"
                    item["word_count"] = words_count
                    item["title"] = item.get("title") or f"NotebookLM ({len(nb_sources)} источников)"
                    all_texts.append(f"=== ИСТОЧНИК: NotebookLM Notebook ({item.get('title', '')}) ===\n{combined_nb}")
                    total_words += words_count
                else:
                    item["status"] = "error"
                    item["error"] = "Не удалось получить данные из NotebookLM"
                fetched_items.append(item)

        if not all_texts:
            return {"skipped": True, "message": "Не удалось извлечь контент из источников", "items": fetched_items}

        # Truncate total to MAX_TOTAL_WORDS
        combined = "\n\n".join(all_texts)
        combined_words = combined.split()
        if len(combined_words) > MAX_TOTAL_WORDS:
            combined = " ".join(combined_words[:MAX_TOTAL_WORDS])
            logger.info(f"Truncated sources to {MAX_TOTAL_WORDS} words (was {len(combined_words)})")

        logger.info(f"Analyzing {len(all_texts)} sources ({total_words} words total)")

        # Send to Claude for analysis
        prompt = f"""Проанализируй эти источники и извлеки данные для YouTube-видео на тему: {self.state.topic}

{combined}

Извлеки ВСЕ полезные факты, цитаты, статистику и инсайты для создания видео."""

        response = self.ask_claude(ANALYSIS_SYSTEM_PROMPT, prompt)

        try:
            extracted = json.loads(response)
        except json.JSONDecodeError:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                extracted = json.loads(response[start:end])
            else:
                extracted = {"raw_response": response}

        # Save updated sources with fetch status
        sources_data["items"] = fetched_items
        sources_data["extracted"] = extracted
        with open(sources_file, "w", encoding="utf-8") as f:
            json.dump(sources_data, f, ensure_ascii=False, indent=2)

        result = {
            "items_count": len(fetched_items),
            "items": fetched_items,
            "total_words_fetched": total_words,
            **extracted,
        }

        logger.info(f"Sources analyzed: {len(extracted.get('facts', []))} facts, "
                     f"{len(extracted.get('quotes', []))} quotes, "
                     f"{len(extracted.get('statistics', []))} stats")
        return result
