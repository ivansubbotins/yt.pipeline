"""YouTube A/B Split-Test Manager.

Manages split-testing of titles and thumbnails for published YouTube videos.
Rotates variants on schedule and tracks performance metrics.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_DIR

logger = logging.getLogger(__name__)

DATA_DIR = BASE_DIR / "data"


def load_test(project_id: str) -> dict | None:
    """Load split-test state for a project."""
    test_file = DATA_DIR / project_id / "splittest.json"
    if test_file.exists():
        return json.loads(test_file.read_text())
    return None


def save_test(project_id: str, test_data: dict):
    """Save split-test state."""
    test_file = DATA_DIR / project_id / "splittest.json"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(json.dumps(test_data, indent=2, ensure_ascii=False))


def start_test(project_id: str, video_id: str, variants: list[dict],
               rotation_hours: int = 6, duration_hours: int = 72) -> dict:
    """
    Start a split-test.

    variants: [{"title": "...", "thumbnail": "/abs/path.jpg"}, ...]
    """
    from youtube_api import YouTubeAPI

    now = datetime.now(timezone.utc).isoformat()

    test_data = {
        "video_id": video_id,
        "status": "running",
        "created_at": now,
        "rotation_hours": rotation_hours,
        "duration_hours": duration_hours,
        "current_variant": 0,
        "last_rotation_at": now,
        "variants": [],
    }

    for v in variants:
        test_data["variants"].append({
            "title": v.get("title", ""),
            "thumbnail": v.get("thumbnail", ""),
            "stats_snapshots": [],
            "total_views_delta": 0,
        })

    # Apply first variant
    yt = YouTubeAPI()
    v0 = variants[0]
    if v0.get("title"):
        yt.update_video(video_id, title=v0["title"])
    if v0.get("thumbnail") and Path(v0["thumbnail"]).exists():
        yt.set_thumbnail(video_id, v0["thumbnail"])

    # Get initial stats
    stats = yt.get_video_stats(video_id)
    test_data["initial_views"] = stats.get("views", 0)

    save_test(project_id, test_data)
    logger.info(f"Split-test started for {project_id}, video {video_id}, {len(variants)} variants")
    return test_data


def rotate(project_id: str) -> dict | None:
    """Check if rotation is due and rotate to next variant."""
    from youtube_api import YouTubeAPI

    test = load_test(project_id)
    if not test or test["status"] != "running":
        return None

    now = datetime.now(timezone.utc)
    last_rotation = datetime.fromisoformat(test["last_rotation_at"])
    created = datetime.fromisoformat(test["created_at"])

    # Check if test duration exceeded
    hours_elapsed = (now - created).total_seconds() / 3600
    if hours_elapsed >= test["duration_hours"]:
        return finish_test(project_id, method="auto")

    # Check if rotation is due
    hours_since_rotation = (now - last_rotation).total_seconds() / 3600
    if hours_since_rotation < test["rotation_hours"]:
        return None  # Not yet time

    yt = YouTubeAPI()
    video_id = test["video_id"]

    # Snapshot current variant stats
    stats = yt.get_video_stats(video_id)
    current_idx = test["current_variant"]
    test["variants"][current_idx]["stats_snapshots"].append({
        "at": now.isoformat(),
        "views": stats.get("views", 0),
    })

    # Calculate views delta for current variant period
    prev_views = test.get("initial_views", 0)
    if test["variants"][current_idx]["stats_snapshots"]:
        # Use previous snapshot if exists
        all_snapshots = []
        for v in test["variants"]:
            all_snapshots.extend(v["stats_snapshots"])
        if len(all_snapshots) > 1:
            sorted_snaps = sorted(all_snapshots, key=lambda s: s["at"])
            prev_views = sorted_snaps[-2]["views"]

    current_views = stats.get("views", 0)
    test["variants"][current_idx]["total_views_delta"] += max(0, current_views - prev_views)

    # Advance to next variant
    next_idx = (current_idx + 1) % len(test["variants"])
    test["current_variant"] = next_idx
    test["last_rotation_at"] = now.isoformat()

    # Apply next variant
    v = test["variants"][next_idx]
    if v.get("title"):
        yt.update_video(video_id, title=v["title"])
    if v.get("thumbnail") and Path(v["thumbnail"]).exists():
        yt.set_thumbnail(video_id, v["thumbnail"])

    save_test(project_id, test)
    logger.info(f"Split-test rotated {project_id}: variant {current_idx} -> {next_idx}")
    return test


def finish_test(project_id: str, method: str = "auto", winner_index: int | None = None) -> dict:
    """Finish split-test and apply winner."""
    from youtube_api import YouTubeAPI

    test = load_test(project_id)
    if not test:
        raise ValueError(f"No split-test found for {project_id}")

    # Determine winner
    if method == "manual" and winner_index is not None:
        best_idx = winner_index
    else:
        # Auto: pick variant with most views delta
        best_idx = 0
        best_views = 0
        for i, v in enumerate(test["variants"]):
            if v["total_views_delta"] > best_views:
                best_views = v["total_views_delta"]
                best_idx = i

    test["status"] = "completed"
    test["completed_at"] = datetime.now(timezone.utc).isoformat()
    test["winner"] = {"index": best_idx, "method": method}

    # Apply winner permanently
    yt = YouTubeAPI()
    winner = test["variants"][best_idx]
    if winner.get("title"):
        yt.update_video(test["video_id"], title=winner["title"])
    if winner.get("thumbnail") and Path(winner["thumbnail"]).exists():
        yt.set_thumbnail(test["video_id"], winner["thumbnail"])

    save_test(project_id, test)
    logger.info(f"Split-test finished for {project_id}, winner: variant {best_idx} ({method})")
    return test


def analyze_test(project_id: str) -> dict:
    """Analyze split-test results with matrix aggregation.

    Returns per-title and per-thumbnail performance if title_index/thumbnail_index
    are present in variants (matrix mode).
    """
    test = load_test(project_id)
    if not test:
        raise ValueError(f"No split-test found for {project_id}")

    result = {"variants": test["variants"], "status": test["status"]}

    # Check if this is a matrix test (variants have title_index and thumbnail_index)
    has_matrix = all("title_index" in v and "thumbnail_index" in v for v in test["variants"])

    if has_matrix:
        # Aggregate by title
        title_perf = {}
        for v in test["variants"]:
            ti = v["title_index"]
            if ti not in title_perf:
                title_perf[ti] = {"title": v["title"], "total_views": 0, "variant_count": 0}
            title_perf[ti]["total_views"] += v.get("total_views_delta", 0)
            title_perf[ti]["variant_count"] += 1

        # Aggregate by thumbnail
        thumb_perf = {}
        for v in test["variants"]:
            ci = v["thumbnail_index"]
            if ci not in thumb_perf:
                thumb_perf[ci] = {"thumbnail": v["thumbnail"], "total_views": 0, "variant_count": 0}
            thumb_perf[ci]["total_views"] += v.get("total_views_delta", 0)
            thumb_perf[ci]["variant_count"] += 1

        # Best title and best thumbnail
        best_title = max(title_perf.values(), key=lambda x: x["total_views"]) if title_perf else None
        best_thumb = max(thumb_perf.values(), key=lambda x: x["total_views"]) if thumb_perf else None

        result["matrix_analysis"] = {
            "by_title": list(title_perf.values()),
            "by_thumbnail": list(thumb_perf.values()),
            "best_title": best_title,
            "best_thumbnail": best_thumb,
        }

    if test.get("winner"):
        result["winner"] = test["winner"]

    return result


def get_all_running() -> list[str]:
    """Get all project IDs with running split-tests."""
    running = []
    if not DATA_DIR.exists():
        return running
    for proj_dir in DATA_DIR.iterdir():
        if proj_dir.is_dir():
            test_file = proj_dir / "splittest.json"
            if test_file.exists():
                try:
                    test = json.loads(test_file.read_text())
                    if test.get("status") == "running":
                        running.append(proj_dir.name)
                except Exception:
                    pass
    return running


# CLI interface
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: splittest.py <start|rotate|finish|status|check-all>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "check-all":
        running = get_all_running()
        print(f"Found {len(running)} running split-tests")
        for pid in running:
            result = rotate(pid)
            if result:
                print(f"  Rotated: {pid}")
            else:
                print(f"  No rotation needed: {pid}")

    elif cmd == "status":
        project_id = sys.argv[2]
        test = load_test(project_id)
        if test:
            print(json.dumps(test, indent=2, ensure_ascii=False))
        else:
            print("No split-test found")

    elif cmd == "finish":
        project_id = sys.argv[2]
        method = sys.argv[3] if len(sys.argv) > 3 else "auto"
        winner_idx = int(sys.argv[4]) if len(sys.argv) > 4 else None
        result = finish_test(project_id, method, winner_idx)
        print(f"Winner: variant {result['winner']['index']}")

    else:
        print(f"Unknown command: {cmd}")
