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


def cmd_generate_cover_custom(args):
    """Generate a single custom cover with specified prompt, style, and text."""
    import json as json_mod
    from config import BASE_DIR, FAL_KEY
    from thumbnail_generator import generate_thumbnail_nano_banana

    params = json_mod.loads(args.params)
    prompt = params.get("prompt", "")
    text_on_image = params.get("text_on_image", "")
    style_id = params.get("style_id", "")
    text_style_id = params.get("text_style_id", "")
    neon_color = params.get("neon_color", "#00BFFF")
    clothing_id = params.get("clothing_id", "")
    clothing_url = params.get("clothing_url", "")

    # Add clothing description to prompt if selected
    if clothing_id:
        gender = "male" if "male:" in clothing_id else "female"
        prompt += f" The person is wearing specific clothing (see reference image for {gender} outfit)."

    # Add text style description to prompt if selected
    TEXT_STYLE_PROMPTS = {
        "brush_script": "brush script handwritten calligraphy text style",
        "thin_condensed": "thin condensed minimal modern uppercase text style",
        "ornamental": "ornamental decorative serif text with curls and swirls",
        "brush_bold": "bold brush italic grunge painted text style",
        "distressed": "distressed grunge worn-out bold uppercase text style",
        "elegant": "thin elegant sophisticated sans-serif text style",
        "classic_serif": "classic serif traditional bold text style",
        "3d_white": "3D white extruded bold text with depth and shadow",
        "red_banner": "bold text on red banner/rectangle background, YouTube style",
        "explosion": "bold text with explosion dust particles smoke effect behind",
        "modern_bold": "modern bold italic sans-serif text style",
        "impact": "extra bold condensed impact uppercase text style",
        "neon_glow": "neon glowing cursive text with blue/cyan light effect",
    }
    if text_style_id and text_style_id in TEXT_STYLE_PROMPTS:
        prompt += f" Text style: {TEXT_STYLE_PROMPTS[text_style_id]}."

    # Find expert photo
    expert_photo = None
    for name in ["expert.jpg", "expert.png"]:
        p = BASE_DIR / "assets" / name
        if p.exists():
            expert_photo = str(p)
            break

    if not expert_photo:
        print("Error: no expert photo found")
        sys.exit(1)

    # Find style image (cover style reference)
    style_image = None
    if style_id:
        # First check built-in references
        ref_map = {
            "dramatic": "dramatic.webp", "bright": "yarkiy.webp", "sport": "sport.webp",
            "glossy": "glyanec.webp", "confidence": "uverennost.webp", "adrenaline": "adrenaline.webp",
            "levitation": "levitation.webp", "at_work": "za_rabotoi.webp", "esoteric": "ezoterika.webp",
            "atmospheric": "athmosphere.webp", "jump": "prizhok.webp", "calm": "spokoiniy.webp",
            "vogue": "vogue.webp", "before_after": "do_posle.webp", "cinema": "cinematic.webp",
            "cyberpunk": "cyberpunk.webp", "giant": "gigantskiy_masshab.webp", "miniature": "miniature-mir.webp",
            "simple": "prostota.webp", "podcast": "podcast.webp", "street": "na_ulice.webp",
            "vulnerable": "vulnerability.webp", "wind": "veter.webp",
        }
        if style_id in ref_map:
            ref_path = BASE_DIR / "assets" / "references" / ref_map[style_id]
            if ref_path.exists():
                style_image = str(ref_path)
        # Fallback to uploaded styles
        if not style_image:
            styles_meta = BASE_DIR / "assets" / "styles" / "styles.json"
            if styles_meta.exists():
                styles = json_mod.loads(styles_meta.read_text(encoding="utf-8"))
                for s in styles:
                    if s["id"] == style_id:
                        sp = BASE_DIR / "assets" / "styles" / s["file"]
                        if sp.exists():
                            style_image = str(sp)
                        break

    # Find clothing image
    clothing_image = None
    if clothing_id:
        # clothing_id format: "male:filename.webp" or "female:filename.webp"
        parts = clothing_id.split(":", 1)
        if len(parts) == 2:
            gender_dir, filename = parts
            cp = BASE_DIR / "assets" / "clothing" / gender_dir / filename
            if cp.exists():
                clothing_image = str(cp)

    # Generate thumbnail
    pipe = Pipeline(args.project_id)
    out_dir = pipe.state.project_dir / "thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Find next available filename
    existing = list(out_dir.glob("custom_*.jpg"))
    idx = len(existing) + 1
    out_path = out_dir / f"custom_{idx}.jpg"

    img = generate_thumbnail_nano_banana(
        prompt, expert_photo,
        style_image_path=style_image,
        clothing_image_path=clothing_image,
    )
    img.save(str(out_path), "JPEG", quality=95)
    print(f"Generated: {out_path}")


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


def cmd_splittest_start(args):
    """Start a split-test from config file."""
    import json as json_mod
    from splittest import start_test
    from config import BASE_DIR

    config_file = BASE_DIR / "data" / args.project_id / "splittest_config.json"
    if not config_file.exists():
        print(f"Error: config file not found: {config_file}")
        sys.exit(1)

    config = json_mod.loads(config_file.read_text())
    result = start_test(
        project_id=args.project_id,
        video_id=config["video_id"],
        variants=config["variants"],
        rotation_hours=config.get("rotation_hours", 6),
        duration_hours=config.get("duration_hours", 72),
    )
    print(f"Split-test started: {len(result['variants'])} variants, rotate every {result['rotation_hours']}h")


def cmd_splittest_finish(args):
    """Finish a split-test."""
    from splittest import finish_test

    result = finish_test(
        project_id=args.project_id,
        method=args.method,
        winner_index=args.winner_index,
    )
    winner = result["winner"]
    print(f"Split-test finished! Winner: variant {winner['index'] + 1} ({winner['method']})")


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

    # generate-cover-custom
    p_gcc = subparsers.add_parser("generate-cover-custom", help="Сгенерировать обложку с кастомным промптом")
    p_gcc.add_argument("project_id", help="ID проекта")
    p_gcc.add_argument("params", help="JSON params: {prompt, text_on_image, style_id, neon_color}")

    # playlists
    subparsers.add_parser("playlists", help="Список плейлистов канала")

    # auth
    subparsers.add_parser("auth", help="Авторизация YouTube API")

    # split-test
    p_st_start = subparsers.add_parser("splittest-start", help="Запустить A/B тест")
    p_st_start.add_argument("project_id", help="ID проекта")

    p_st_finish = subparsers.add_parser("splittest-finish", help="Завершить A/B тест")
    p_st_finish.add_argument("project_id", help="ID проекта")
    p_st_finish.add_argument("method", nargs="?", default="auto", help="auto или manual")
    p_st_finish.add_argument("winner_index", nargs="?", type=int, default=None, help="Индекс победителя (для manual)")

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
        "generate-cover-custom": cmd_generate_cover_custom,
        "publish": cmd_publish,
        "playlists": cmd_playlists,
        "auth": cmd_auth,
        "splittest-start": cmd_splittest_start,
        "splittest-finish": cmd_splittest_finish,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
