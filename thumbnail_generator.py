"""YouTube thumbnail generator.

Primary mode: Nano Banana 2 (fal.ai) — full thumbnail in one pass
(expert photo + prompt → complete thumbnail with person, text, effects).

Fallback: 3-layer system (Recraft scene + rembg cutout + Pillow text).

Final output: 1280x720 JPEG
"""

import io
import logging
import math
import os
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, RECRAFT_API_KEY, FAL_KEY

logger = logging.getLogger(__name__)

# ---------- FONTS ----------

# Bold fonts for thumbnail text — prefer Impact-style
FONT_CANDIDATES = [
    # Custom fonts (download to project)
    str(Path(__file__).parent / "fonts" / "Impact.ttf"),
    str(Path(__file__).parent / "fonts" / "Bebas-Regular.ttf"),
    str(Path(__file__).parent / "fonts" / "Oswald-Bold.ttf"),
    # System fallbacks
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]

RECRAFT_API_URL = "https://external.api.recraft.ai/v1/images/generations"
RECRAFT_I2I_URL = "https://external.api.recraft.ai/v1/images/imageToImage"

MASTER_PROMPT = (
    "Keep the person's face identical to the uploaded photo. "
    "Create a vibrant, clickable YouTube thumbnail with the person. "
    "If text is specified, render it exactly as described — correct spelling, position, and style. "
    "All on-image text must be in Cyrillic (Russian alphabet), NEVER Latin/English. "
    "If the scene shows money or banknotes, they MUST be Russian 5000-ruble notes "
    "(distinctive purple/violet color with Khabarov monument, NOT US dollars, NOT euros). "
    "Russian audience — Russian visual context."
)


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return (255, 255, 255)
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


# ============================================================
# LAYER 1: Scene/Background generation
# ============================================================

def generate_scene_recraft(prompt: str) -> Image.Image:
    """Generate cartoon scene using Recraft API."""
    headers = {
        "Authorization": f"Bearer {RECRAFT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "style": "digital_illustration",
        "size": "1820x1024",
        "response_format": "url",
    }
    logger.info(f"Recraft: generating scene — {prompt[:80]}...")
    resp = requests.post(RECRAFT_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()

    image_url = resp.json()["data"][0]["url"]
    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()

    img = Image.open(io.BytesIO(img_resp.content))
    img = img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
    return img


def generate_thumbnail_nano_banana(
    prompt: str,
    expert_photo_path: str | Path,
    style_image_path: str | Path | None = None,
    clothing_image_path: str | Path | None = None,
) -> Image.Image:
    """Generate full thumbnail using Nano Banana 2 (fal.ai).

    Sends the expert photo + optional style reference + optional clothing reference + prompt.
    Nano Banana preserves facial features and generates the complete thumbnail.
    """
    import fal_client

    os.environ["FAL_KEY"] = FAL_KEY
    full_prompt = f"{MASTER_PROMPT} {prompt}"

    # Upload expert photo to fal storage
    image_urls = []
    photo_url = fal_client.upload_file(str(expert_photo_path))
    image_urls.append(photo_url)
    logger.info(f"Expert photo uploaded: {photo_url}")

    # Upload style reference if provided
    if style_image_path and Path(style_image_path).exists():
        style_url = fal_client.upload_file(str(style_image_path))
        image_urls.append(style_url)
        full_prompt += " Use the visual style, color palette, and composition from the second reference image."
        logger.info(f"Style reference uploaded: {style_url}")

    # Upload clothing reference if provided
    if clothing_image_path and Path(clothing_image_path).exists():
        clothing_url = fal_client.upload_file(str(clothing_image_path))
        image_urls.append(clothing_url)
        ref_num = len(image_urls)
        full_prompt += f" Dress the person exactly as shown in reference image #{ref_num} (clothing reference)."
        logger.info(f"Clothing reference uploaded: {clothing_url}")

    logger.info(f"Nano Banana 2: prompt={full_prompt[:120]}... ({len(image_urls)} images)")

    result = fal_client.subscribe("fal-ai/nano-banana-pro/edit", arguments={
        "prompt": full_prompt,
        "image_urls": image_urls,
        "aspect_ratio": "16:9",
        "output_format": "jpeg",
        "num_images": 1,
    })

    image_url = result["images"][0]["url"]
    logger.info(f"Nano Banana 2 result: {image_url}")

    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()

    img = Image.open(io.BytesIO(img_resp.content))
    img = img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
    return img


def edit_thumbnail_with_marks(
    marked_image_path: str | Path,
    user_instruction: str,
    expert_photo_path: str | Path | None = None,
) -> Image.Image:
    """Region-edit a thumbnail using Nano Banana 2 with user-painted marks as visual hints.

    The marked_image_path is the current thumbnail with the user's brush strokes burned in
    (as a flat composite). We send it to Nano Banana with an instruction telling the model
    to act on the marked area and remove the marks afterward.

    user_instruction examples:
      - 'удали этот текст'
      - 'нарисуй стоматологическое кресло'
      - 'замени слово на ШОК'
    """
    import fal_client

    os.environ["FAL_KEY"] = FAL_KEY

    # Upload the marked image (current thumbnail + user's brush strokes flattened)
    img_url = fal_client.upload_file(str(marked_image_path))
    logger.info(f"Marked image uploaded: {img_url}")

    image_urls = [img_url]
    if expert_photo_path and Path(expert_photo_path).exists():
        expert_url = fal_client.upload_file(str(expert_photo_path))
        image_urls.append(expert_url)
        logger.info(f"Expert reference uploaded: {expert_url}")

    # Compose an instruction-based edit prompt.
    # The model gets: original-with-marks + an instruction that uses the marks as
    # location hints and explicitly asks to remove the strokes from the output.
    expert_clause = (
        " Image #2 is a reference for the person's face — keep their identity identical."
        if len(image_urls) > 1 else ""
    )
    full_prompt = (
        f"{MASTER_PROMPT} "
        "The user has drawn coloured brush strokes on the image to mark a region. "
        f"Apply this edit to the marked region: {user_instruction}. "
        "After applying the edit, OUTPUT A CLEAN IMAGE — the brush strokes/marks must be "
        "completely removed from the final result. Do not modify other parts of the image. "
        "Preserve the original composition, lighting, and the rest of the scene."
        f"{expert_clause}"
    )

    logger.info(f"Region-edit Nano Banana: instruction={user_instruction[:80]}...")

    result = fal_client.subscribe("fal-ai/nano-banana-pro/edit", arguments={
        "prompt": full_prompt,
        "image_urls": image_urls,
        "aspect_ratio": "16:9",
        "output_format": "jpeg",
        "num_images": 1,
    })

    image_url = result["images"][0]["url"]
    logger.info(f"Region-edit result: {image_url}")

    img_resp = requests.get(image_url, timeout=60)
    img_resp.raise_for_status()
    img = Image.open(io.BytesIO(img_resp.content))
    img = img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
    return img


def generate_scene_gradient(colors: list[str]) -> Image.Image:
    """Fallback: generate a gradient background."""
    img = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
    draw = ImageDraw.Draw(img)

    if len(colors) < 2:
        colors = ["#1a1a2e", "#e94560"]

    rgb_colors = [_hex_to_rgb(c) for c in colors[:3]]

    for y in range(THUMBNAIL_HEIGHT):
        t = y / THUMBNAIL_HEIGHT
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
        draw.line([(0, y), (THUMBNAIL_WIDTH, y)], fill=(r, g, b))

    return img


# ============================================================
# LAYER 2: Expert photo overlay
# ============================================================

def remove_background(image: Image.Image) -> Image.Image:
    """Remove background from expert photo using rembg."""
    try:
        from rembg import remove
        output = remove(image)
        return output  # RGBA with transparent background
    except ImportError:
        logger.warning("rembg not installed — using simple crop (pip install rembg)")
        # Fallback: just return as RGBA
        return image.convert("RGBA")


def load_expert_photo(photo_path: str | Path) -> Image.Image | None:
    """Load and prepare expert photo with background removal."""
    path = Path(photo_path)
    if not path.exists():
        logger.warning(f"Expert photo not found: {path}")
        return None

    img = Image.open(path).convert("RGB")
    # Remove background
    cutout = remove_background(img)
    return cutout


def paste_expert(
    canvas: Image.Image,
    expert: Image.Image,
    position: str = "right",
    scale: float = 0.75,
) -> Image.Image:
    """Paste expert cutout onto the canvas.

    Args:
        canvas: 1280x720 background
        expert: RGBA image with transparent background
        position: "left", "right", or "center"
        scale: how much of the canvas height the expert should fill (0.0–1.0)
    """
    canvas = canvas.convert("RGBA")

    # Scale expert to fit canvas height
    target_h = int(THUMBNAIL_HEIGHT * scale)
    aspect = expert.width / expert.height
    target_w = int(target_h * aspect)
    expert_resized = expert.resize((target_w, target_h), Image.LANCZOS)

    # Position
    y = THUMBNAIL_HEIGHT - target_h  # align to bottom
    if position == "left":
        x = -int(target_w * 0.1)  # slightly off-edge for dynamic feel
    elif position == "right":
        x = THUMBNAIL_WIDTH - target_w + int(target_w * 0.1)
    else:  # center
        x = (THUMBNAIL_WIDTH - target_w) // 2

    canvas.paste(expert_resized, (x, y), expert_resized)
    return canvas.convert("RGB")


# ============================================================
# LAYER 3: Text overlay (bold Russian text with outline)
# ============================================================

def _draw_text_outlined(
    draw: ImageDraw.Draw,
    position: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    stroke_fill: tuple,
    stroke_width: int = 6,
):
    """Draw text with thick outline for maximum readability."""
    x, y = position
    # Outer stroke
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx * dx + dy * dy <= stroke_width * stroke_width:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    # Inner text
    draw.text(position, text, font=font, fill=fill)


def render_text_overlay(
    img: Image.Image,
    text: str,
    position: str = "top-left",
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    font_size: int | None = None,
    expert_position: str = "right",
) -> Image.Image:
    """Render bold text on thumbnail, avoiding the expert area.

    text: 2-4 words, will be UPPERCASED automatically
    position: where to place text
    expert_position: where the expert is, so text avoids that area
    """
    img = img.copy()
    draw = ImageDraw.Draw(img)

    text = text.upper().strip()
    words = text.split()

    # Auto font size based on text length
    if font_size is None:
        if len(words) <= 2:
            font_size = 110
        elif len(words) <= 3:
            font_size = 95
        else:
            font_size = 80

    font = _find_font(font_size)

    # Split into lines (max 2 words per line for impact)
    lines = []
    words_per_line = 2 if len(words) > 2 else len(words)
    for i in range(0, len(words), words_per_line):
        lines.append(" ".join(words[i : i + words_per_line]))

    # Calculate text block dimensions
    line_bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [bb[3] - bb[1] for bb in line_bboxes]
    line_widths = [bb[2] - bb[0] for bb in line_bboxes]
    spacing = 10
    total_h = sum(line_heights) + spacing * (len(lines) - 1)
    max_w = max(line_widths) if line_widths else 0

    # Available area (avoid expert)
    margin = 40
    if expert_position == "right":
        # Text goes to the left
        avail_x_start = margin
        avail_x_end = int(THUMBNAIL_WIDTH * 0.55)
    elif expert_position == "left":
        avail_x_start = int(THUMBNAIL_WIDTH * 0.45)
        avail_x_end = THUMBNAIL_WIDTH - margin
    else:
        avail_x_start = margin
        avail_x_end = THUMBNAIL_WIDTH - margin

    # Vertical position
    if "top" in position:
        start_y = margin + 20
    elif "bottom" in position:
        start_y = THUMBNAIL_HEIGHT - total_h - margin - 20
    else:
        start_y = (THUMBNAIL_HEIGHT - total_h) // 2

    fill_rgb = _hex_to_rgb(text_color)
    stroke_rgb = _hex_to_rgb(stroke_color)

    y = start_y
    for i, line in enumerate(lines):
        lw = line_widths[i]
        # Center text within available area
        x = avail_x_start + (avail_x_end - avail_x_start - lw) // 2

        _draw_text_outlined(
            draw, (x, y), line, font,
            fill=fill_rgb,
            stroke_fill=stroke_rgb,
            stroke_width=6,
        )
        y += line_heights[i] + spacing

    return img


# ============================================================
# COMPOSITE: Assemble all 3 layers
# ============================================================

def generate_thumbnail(
    thumbnail_spec: dict,
    output_path: Path,
    use_recraft: bool = True,
    expert_photo_path: str | Path | None = None,
    use_i2i: bool = True,
    style_image_path: str | Path | None = None,
) -> Path:
    """Generate a thumbnail.

    If use_i2i=True and expert_photo_path is provided, uses Nano Banana 2
    to generate the full thumbnail in one pass (person + scene + text).
    If style_image_path is provided, it's used as a style reference.
    Otherwise falls back to the 3-layer system (scene + rembg + Pillow text).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scene_prompt = (
        thumbnail_spec.get("generation_prompt") or
        thumbnail_spec.get("scene_description", "")
    )

    # --- Mode 1: Nano Banana 2 (full thumbnail in one pass) ---
    if use_i2i and expert_photo_path and FAL_KEY and scene_prompt:
        try:
            canvas = generate_thumbnail_nano_banana(
                scene_prompt, expert_photo_path,
                style_image_path=style_image_path,
            )
            canvas.save(output_path, "JPEG", quality=95)
            logger.info(f"Nano Banana thumbnail saved: {output_path}")
            return output_path
        except Exception as e:
            logger.warning(f"Nano Banana failed, falling back to 3-layer: {e}")

    # --- Mode 2: 3-layer fallback ---
    colors = thumbnail_spec.get("colors") or thumbnail_spec.get("background_colors", [])

    if use_recraft and RECRAFT_API_KEY and scene_prompt:
        try:
            canvas = generate_scene_recraft(scene_prompt)
        except Exception as e:
            logger.warning(f"Recraft failed, using gradient: {e}")
            canvas = generate_scene_gradient(colors or ["#1a1a2e", "#e94560"])
    else:
        canvas = generate_scene_gradient(colors or ["#1a1a2e", "#e94560"])

    # Layer 2: Expert photo
    expert_pos = thumbnail_spec.get("expert_position", "right")
    if expert_photo_path and expert_pos != "none":
        expert = load_expert_photo(expert_photo_path)
        if expert:
            canvas = paste_expert(canvas, expert, position=expert_pos, scale=0.8)
            logger.info(f"Expert photo placed: {expert_pos}")

    # Layer 3: Text overlay
    text = thumbnail_spec.get("text_overlay", "")
    if text:
        text_pos = thumbnail_spec.get("text_position", "top-left")
        text_color = thumbnail_spec.get("text_color", "#FFFFFF")
        stroke_color = thumbnail_spec.get("text_stroke_color", "#000000")

        canvas = render_text_overlay(
            canvas,
            text=text,
            position=text_pos,
            text_color=text_color,
            stroke_color=stroke_color,
            expert_position=expert_pos,
        )

    canvas.save(output_path, "JPEG", quality=95)
    logger.info(f"Thumbnail saved: {output_path} ({THUMBNAIL_WIDTH}x{THUMBNAIL_HEIGHT})")
    return output_path


def generate_all_variants(
    thumbnails: list[dict],
    output_dir: Path,
    use_recraft: bool = True,
    expert_photo_path: str | Path | None = None,
    use_i2i: bool = True,
    style_image_path: str | Path | None = None,
) -> list[Path]:
    """Generate all thumbnail variants."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for i, spec in enumerate(thumbnails):
        filename = f"thumbnail_{i + 1}.jpg"
        output_path = output_dir / filename
        path = generate_thumbnail(
            spec, output_path,
            use_recraft=use_recraft,
            expert_photo_path=expert_photo_path,
            use_i2i=use_i2i,
            style_image_path=style_image_path,
        )
        paths.append(path)
        logger.info(f"Generated variant {i + 1}/{len(thumbnails)}: {path}")

    return paths
