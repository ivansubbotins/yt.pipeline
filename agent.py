#!/usr/bin/env python3
"""YouTube Pipeline Agent — CLI entry point.

Usage:
  python agent.py new <topic>                   Create new video project
  python agent.py run <project_id>              Run all auto steps (1-7)
  python agent.py step <project_id> <step>      Run a specific step
  python agent.py status <project_id>           Show project status
  python agent.py review <project_id>           Generate review for Ivan
  python agent.py list                          List all projects
  python agent.py shot-done <project_id>        Mark shooting as done
  python agent.py edit-done <project_id> [file] Mark editing as done
  python agent.py publish <project_id> --approve Publish (requires approval)
                   [--schedule ISO8601] [--playlist ID] [--category ID]
  python agent.py playlists                     List channel playlists
  python agent.py auth                          Authenticate with YouTube
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, LOGS_DIR
from pipeline import Pipeline
from state import PipelineState, list_projects, PIPELINE_STEPS
from youtube_api import YouTubeAPI


def setup_logging():
    LOGS_DIR.mkdir(exist_ok=True)
    log_file = LOGS_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def generate_project_id(topic: str) -> str:
    """Generate a project ID from topic and date."""
    slug = topic.lower()[:40]
    # Simple transliteration for cyrillic
    tr = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", " ": "-",
    }
    slug = "".join(tr.get(c, c) for c in slug)
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    slug = slug.strip("-")
    date = datetime.now().strftime("%Y%m%d")
    return f"{date}-{slug}"


def cmd_new(args):
    topic = " ".join(args.topic)
    project_id = generate_project_id(topic)

    pipe = Pipeline(project_id, topic=topic)
    print(f"Создан проект: {project_id}")
    print(f"Тема: {topic}")
    print(f"Директория: {pipe.state.project_dir}")
    print(f"\nЗапустите: python agent.py run {project_id}")


def cmd_run(args):
    pipe = Pipeline(args.project_id)
    if not pipe.state.topic:
        print("Ошибка: тема не задана. Создайте проект через 'new'.")
        return

    print(f"Запуск автоматических шагов для: {pipe.state.topic}")
    print("=" * 60)

    results = pipe.run_auto_steps()

    print("\n" + "=" * 60)
    print(f"Выполнено шагов: {len(results)}")
    print()

    # Export review
    review_file = pipe.export_for_review()
    print(f"Ревью для Ивана: {review_file}")
    print()
    print(pipe.state.summary())
    print()
    print("Следующий шаг: съёмка (ручной этап)")
    print(f"После съёмки: python agent.py shot-done {args.project_id}")


def cmd_step(args):
    if args.step not in PIPELINE_STEPS:
        print(f"Неизвестный шаг: {args.step}")
        print(f"Доступные шаги: {', '.join(PIPELINE_STEPS)}")
        return

    pipe = Pipeline(args.project_id)
    result = pipe.run_step(args.step)
    print(f"Шаг '{args.step}' выполнен.")
    print(f"Результат: {list(result.keys()) if isinstance(result, dict) else result}")


def cmd_status(args):
    state = PipelineState(args.project_id)
    print(state.summary())


def cmd_review(args):
    pipe = Pipeline(args.project_id)
    review = pipe.review()
    print(review)

    review_file = pipe.export_for_review()
    print(f"\nФайл ревью: {review_file}")


def cmd_list(_args):
    projects = list_projects()
    if not projects:
        print("Проектов нет. Создайте: python agent.py new <тема>")
        return

    print(f"Проекты ({len(projects)}):")
    for pid in sorted(projects):
        state = PipelineState(pid)
        status = state.current_step
        topic = state.topic or "—"
        print(f"  {pid}: [{status}] {topic}")


def cmd_shot_done(args):
    pipe = Pipeline(args.project_id)
    pipe.resume_after_shooting()
    print("Съёмка отмечена как завершённая.")
    print(f"Следующий шаг: монтаж (ручной этап)")
    print(f"После монтажа: python agent.py edit-done {args.project_id} [path/to/video.mp4]")


def cmd_edit_done(args):
    pipe = Pipeline(args.project_id)
    pipe.resume_after_editing(video_file=args.video_file)
    print("Монтаж отмечен как завершённый.")
    if args.video_file:
        print(f"Видеофайл: {args.video_file}")
    print(f"\nГотово к публикации!")
    print(f"Ревью: python agent.py review {args.project_id}")
    print(f"Публикация: python agent.py publish {args.project_id} --approve")


def cmd_publish(args):
    if not args.approve:
        print("Публикация требует утверждения Иваном.")
        print(f"Добавьте --approve: python agent.py publish {args.project_id} --approve")
        print()
        print("Дополнительные опции:")
        print(f"  --schedule '2026-03-15T14:00:00Z'  Запланировать публикацию")
        print(f"  --playlist <playlist_id>            Добавить в плейлист")
        print(f"  --category <category_id>            ID категории YouTube")
        return

    pipe = Pipeline(args.project_id)
    result = pipe.publish(
        approved=True,
        schedule=args.schedule,
        playlist_id=args.playlist,
        category_id=args.category,
    )

    if result.get("status") == "blocked":
        print(result["message"])
    else:
        print(f"Видео загружено: {result.get('url', '—')}")
        print(f"  Заголовок: {result.get('title', '—')}")
        print(f"  Теги: {len(result.get('tags', []))} шт.")
        print(f"  Категория: {result.get('category_id', '—')}")
        print(f"  Обложка: {'да' if result.get('has_thumbnail') else 'нет'}")
        print(f"  Описание: {'да' if result.get('has_description') else 'нет'}")

        if result.get("publish_at"):
            print(f"  Запланировано: {result['publish_at']}")
        else:
            print("  Статус: private (измените в YouTube Studio или запланируйте)")

        if result.get("playlist_id"):
            print(f"  Плейлист: {result['playlist_id']}")
        if result.get("playlist_error"):
            print(f"  Ошибка плейлиста: {result['playlist_error']}")


def cmd_playlists(_args):
    yt = YouTubeAPI()
    playlists = yt.get_playlists()
    if not playlists:
        print("Плейлисты не найдены.")
        return

    print(f"Плейлисты ({len(playlists)}):")
    for p in playlists:
        desc = f" — {p['description']}" if p["description"] else ""
        print(f"  {p['id']}: {p['title']}{desc}")


def cmd_auth(_args):
    yt = YouTubeAPI()
    yt.authenticate()
    info = yt.get_channel_info()
    if info:
        print(f"Авторизация успешна!")
        print(f"Канал: {info['title']}")
        print(f"Подписчики: {info.get('subscribers', '—')}")
        print(f"URL: {info['url']}")
    else:
        print("Авторизация выполнена, но канал не найден.")


def main():
    parser = argparse.ArgumentParser(description="YouTube Pipeline Agent")
    subparsers = parser.add_subparsers(dest="command", help="Команда")

    # new
    p_new = subparsers.add_parser("new", help="Создать новый проект")
    p_new.add_argument("topic", nargs="+", help="Тема видео")

    # run
    p_run = subparsers.add_parser("run", help="Запустить автоматические шаги")
    p_run.add_argument("project_id", help="ID проекта")

    # step
    p_step = subparsers.add_parser("step", help="Запустить конкретный шаг")
    p_step.add_argument("project_id", help="ID проекта")
    p_step.add_argument("step", help="Название шага")

    # status
    p_status = subparsers.add_parser("status", help="Статус проекта")
    p_status.add_argument("project_id", help="ID проекта")

    # review
    p_review = subparsers.add_parser("review", help="Ревью для утверждения")
    p_review.add_argument("project_id", help="ID проекта")

    # list
    subparsers.add_parser("list", help="Список проектов")

    # shot-done
    p_shot = subparsers.add_parser("shot-done", help="Отметить съёмку завершённой")
    p_shot.add_argument("project_id", help="ID проекта")

    # edit-done
    p_edit = subparsers.add_parser("edit-done", help="Отметить монтаж завершённым")
    p_edit.add_argument("project_id", help="ID проекта")
    p_edit.add_argument("video_file", nargs="?", default=None, help="Путь к видеофайлу")

    # publish
    p_pub = subparsers.add_parser("publish", help="Опубликовать видео")
    p_pub.add_argument("project_id", help="ID проекта")
    p_pub.add_argument("--approve", action="store_true", help="Подтвердить публикацию")
    p_pub.add_argument("--schedule", default=None, help="Дата публикации (ISO 8601, напр. 2026-03-15T14:00:00Z)")
    p_pub.add_argument("--playlist", default=None, help="ID плейлиста YouTube")
    p_pub.add_argument("--category", default=None, help="ID категории YouTube (напр. 27=Education)")

    # playlists
    subparsers.add_parser("playlists", help="Список плейлистов канала")

    # auth
    subparsers.add_parser("auth", help="Авторизация YouTube API")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    setup_logging()

    commands = {
        "new": cmd_new,
        "run": cmd_run,
        "step": cmd_step,
        "status": cmd_status,
        "review": cmd_review,
        "list": cmd_list,
        "shot-done": cmd_shot_done,
        "edit-done": cmd_edit_done,
        "publish": cmd_publish,
        "playlists": cmd_playlists,
        "auth": cmd_auth,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
