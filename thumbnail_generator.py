"""Thumbnail image generator — creates 1280x720 clickbait-style YouTube thumbnails.

Supports two modes:
1. Recraft API — AI-generated backgrounds with Pillow text overlay
2. Pillow-only fallback — gradient backgrounds with styled text (no API needed)
"""

import io
import json
import logging
import math
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, RECRAFT_API_KEY

logger = logging.getLogger(__name__)

# Font candidates — system fonts commonly available on Linux
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]

RECRAFT_API_URL = "https://external.api.recraft.ai/v1/images/generations"


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    """Find an available bold system font."""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return (255, 255, 255)
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _generate_gradient(
    width: int, height: int, colors: list[str]
) -> Image.Image:
    """Generate a gradient background from a list of hex colors."""
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    if len(colors) < 2:
        colors = ["#1a1a2e", "#e94560"]

    rgb_colors = [_hex_to_rgb(c) for c in colors[:3]]

    for y in range(height):
        t = y / height
        if len(rgb_colors) == 2:
            r = int(rgb_colors[0][0] + (rgb_colors[1][0] - rgb_colors[0][0]) * t)
            g = int(rgb_colors[0][1] + (rgb_colors[1][1] - rgb_colors[0][1]) * t)
            b = int(rgb_colors[0][2] + (rgb_colors[1][2] - rgb_colors[0][2]) * t)
        else:
            if t < 0.5:
                t2 = t * 2
                r = int(rgb_colors[0][0] + (rgb_colors[1][0] - rgb_colors[0][0]) * t2)
                g = int(rgb_colors[0][1] + (rgb_colors[1][1] - rgb_colors[0][1]) * t2)
                b = int(rgb_colors[0][2] + (rgb_colors[1][2] - rgb_colors[0][2]) * t2)
            else:
                t2 = (t - 0.5) * 2
                r = int(rgb_colors[1][0] + (rgb_colors[2][0] - rgb_colors[1][0]) * t2)
                g = int(rgb_colors[1][1] + (rgb_colors[2][1] - rgb_colors[1][1]) * t2)
                b = int(rgb_colors[1][2] + (rgb_colors[2][2] - rgb_colors[1][2]) * t2)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    return img


def _add_vignette(img: Image.Image) -> Image.Image:
    """Add a subtle dark vignette around edges for depth."""
    width, height = img.size
    vignette = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(vignette)

    cx, cy = width // 2, height // 2
    max_dist = math.sqrt(cx**2 + cy**2)

    for y in range(0, height, 2):
        for x in range(0, width, 2):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            alpha = int(min(255, (dist / max_dist) * 180))
            draw.rectangle([x, y, x + 1, y + 1], fill=alpha)

    dark = Image.new("RGB", (width, height), (0, 0, 0))
    img = Image.composite(dark, img, vignette)
    return img


def _draw_text_with_stroke(
    draw: ImageDraw.Draw,
    position: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    stroke_fill: tuple[int, int, int],
    stroke_width: int = 4,
):
    """Draw text with an outline/stroke for readability."""
    x, y = position
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx * dx + dy * dy <= stroke_width * stroke_width:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text(position, text, font=font, fill=fill)


def _calculate_text_position(
    img_width: int,
    img_height: int,
    text_bbox: tuple[int, int, int, int],
    position: str,
) -> tuple[int, int]:
    """Calculate text position based on named position string."""
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    margin = 40

    positions = {
        "top-left": (margin, margin),
        "top-right": (img_width - text_w - margin, margin),
        "top-center": ((img_width - text_w) // 2, margin),
        "center": ((img_width - text_w) // 2, (img_height - text_h) // 2),
        "bottom-left": (margin, img_height - text_h - margin),
        "bottom-right": (img_width - text_w - margin, img_height - text_h - margin),
        "bottom-center": ((img_width - text_w) // 2, img_height - text_h - margin),
    }
    return positions.get(position, positions["center"])


def _render_text_overlay(
    img: Image.Image,
    text: str,
    position: str = "center",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    font_size: int | None = None,
) -> Image.Image:
    """Render large clickbait text overlay on the image."""
    img = img.copy()
    draw = ImageDraw.Draw(img)

    if font_size is None:
        font_size = max(60, min(120, img.width // (len(text) // 2 + 1)))
        font_size = min(font_size, 140)

    font = _find_font(font_size)

    # Word-wrap if text is too wide
    words = text.upper().split()
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] > img.width * 0.85 and current_line:
            lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)
    if current_line:
        lines.append(" ".join(current_line))

    # Calculate total text block height
    line_bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [bb[3] - bb[1] for bb in line_bboxes]
    line_spacing = 10
    total_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    # Find starting y position
    full_bbox = (0, 0, max(bb[2] - bb[0] for bb in line_bboxes), total_height)
    base_x, base_y = _calculate_text_position(
        img.width, img.height, full_bbox, position
    )

    fill_rgb = _hex_to_rgb(text_color)
    stroke_rgb = _hex_to_rgb(stroke_color)

    y_offset = base_y
    for i, line in enumerate(lines):
        line_bbox = draw.textbbox((0, 0), line, font=font)
        line_w = line_bbox[2] - line_bbox[0]
        # Center each line horizontally relative to the text block
        x = (img.width - line_w) // 2 if position == "center" else base_x

        _draw_text_with_stroke(
            draw, (x, y_offset), line, font, fill_rgb, stroke_rgb, stroke_width=5
        )
        y_offset += line_heights[i] + line_spacing

    return img


def generate_with_recraft(
    prompt: str,
    output_path: Path,
    text_overlay: str = "",
    text_position: str = "center",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
) -> Path:
    """Generate a thumbnail using Recraft API + Pillow text overlay."""
    headers = {
        "Authorization": f"Bearer {RECRAFT_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "prompt": prompt,
        "style": "digital_illustration",
        "size": "1280x720",
        "response_format": "url",
    }

    logger.info(f"Requesting Recraft image generation: {prompt[:80]}...")
    resp = requests.post(RECRAFT_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()

    result = resp.json()
    image_url = result["data"][0]["url"]

    # Download the generated image
    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()

    img = Image.open(io.BytesIO(img_resp.content))
    img = img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)

    # Add text overlay
    if text_overlay:
        img = _render_text_overlay(
            img, text_overlay, text_position, text_color, stroke_color
        )

    img.save(output_path, "JPEG", quality=95)
    logger.info(f"Saved Recraft thumbnail: {output_path}")
    return output_path


def generate_with_pillow(
    colors: list[str],
    output_path: Path,
    text_overlay: str = "",
    text_position: str = "center",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    description: str = "",
) -> Path:
    """Generate a thumbnail using Pillow only (gradient + text). Fallback mode."""
    img = _generate_gradient(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, colors)

    # Add geometric shapes for visual interest
    draw = ImageDraw.Draw(img)
    # Diagonal accent stripe
    accent_color = _hex_to_rgb(colors[0] if colors else "#e94560")
    accent_bright = tuple(min(255, c + 80) for c in accent_color)
    for i in range(80):
        alpha = 1.0 - (i / 80)
        c = tuple(int(ac * alpha + bc * (1 - alpha)) for ac, bc in zip(accent_bright, (0, 0, 0)))
        draw.line(
            [(THUMBNAIL_WIDTH - 400 + i * 3, 0), (THUMBNAIL_WIDTH + i * 3, THUMBNAIL_HEIGHT)],
            fill=c,
            width=3,
        )

    # Add semi-transparent dark band behind text for readability
    band_y = THUMBNAIL_HEIGHT // 2 - 100
    overlay = Image.new("RGBA", (THUMBNAIL_WIDTH, 200), (0, 0, 0, 140))
    img = img.convert("RGBA")
    img.paste(overlay, (0, band_y), overlay)
    img = img.convert("RGB")

    # Add vignette
    img = _add_vignette(img)

    # Add text overlay
    if text_overlay:
        img = _render_text_overlay(
            img, text_overlay, text_position, text_color, stroke_color
        )

    img.save(output_path, "JPEG", quality=95)
    logger.info(f"Saved Pillow thumbnail: {output_path}")
    return output_path


def generate_thumbnail(
    thumbnail_spec: dict,
    output_path: Path,
    use_recraft: bool = True,
) -> Path:
    """Generate a single thumbnail from a spec dict.

    Args:
        thumbnail_spec: dict with keys:
            - generation_prompt: str (for Recraft)
            - text_overlay: str
            - text_position: str
            - text_color: str
            - text_stroke_color: str
            - colors: list[str] (for Pillow fallback)
            - description: str (optional)
        output_path: where to save the JPEG
        use_recraft: whether to try Recraft API first
    """
    text_overlay = thumbnail_spec.get("text_overlay", "")
    text_position = thumbnail_spec.get("text_position", "center")
    text_color = thumbnail_spec.get("text_color", "#FFFFFF")
    stroke_color = thumbnail_spec.get("text_stroke_color", "#000000")

    if use_recraft and RECRAFT_API_KEY:
        try:
            return generate_with_recraft(
                prompt=thumbnail_spec.get("generation_prompt", ""),
                output_path=output_path,
                text_overlay=text_overlay,
                text_position=text_position,
                text_color=text_color,
                stroke_color=stroke_color,
            )
        except Exception as e:
            logger.warning(f"Recraft API failed, falling back to Pillow: {e}")

    # Pillow fallback
    colors = thumbnail_spec.get("colors", ["#1a1a2e", "#e94560", "#0f3460"])
    return generate_with_pillow(
        colors=colors,
        output_path=output_path,
        text_overlay=text_overlay,
        text_position=text_position,
        text_color=text_color,
        stroke_color=stroke_color,
        description=thumbnail_spec.get("description", ""),
    )


def generate_all_variants(
    thumbnails: list[dict],
    output_dir: Path,
    use_recraft: bool = True,
) -> list[Path]:
    """Generate all thumbnail variants and return list of file paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for i, spec in enumerate(thumbnails):
        filename = f"thumbnail_{i + 1}.jpg"
        output_path = output_dir / filename
        path = generate_thumbnail(spec, output_path, use_recraft=use_recraft)
        paths.append(path)
        logger.info(f"Generated variant {i + 1}/{len(thumbnails)}: {path}")

    return paths
